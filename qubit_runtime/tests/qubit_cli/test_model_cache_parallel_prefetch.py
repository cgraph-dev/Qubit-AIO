"""Tests for parallel model-catalog prefetch and thread-safe cache writes.

Regression tests for the serial /v1/models bottleneck: when the 1h disk cache
lapses, ``list_authenticated_providers()`` previously fetched each authed
provider's model list serially. With 10+ providers this stacked to 15-30s of
blocking HTTP round-trips. The parallel prefetch warms stale cache entries
concurrently via ThreadPoolExecutor before the serial picker loop starts.
"""

from __future__ import annotations

import time

import pytest
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Thread-safe cache entry update (qubit_cli/models.py)
# ---------------------------------------------------------------------------

class TestUpdateProviderCacheEntry:
    """Verify ``update_provider_cache_entry`` writes safely under concurrency."""

    def test_writes_new_entry(self, tmp_path, monkeypatch):
        """A new entry is persisted to the cache file."""
        import qubit_cli.models as mod

        cache_path = tmp_path / "provider_models_cache.json"
        monkeypatch.setattr(mod, "_provider_models_cache_path", lambda: cache_path)

        with patch.object(mod, "_credential_fingerprint", return_value="fp1"):
            mod.update_provider_cache_entry("openrouter", ["m1", "m2"])

        cache = mod._load_provider_models_cache()
        assert "openrouter" in cache
        assert cache["openrouter"]["models"] == ["m1", "m2"]
        assert cache["openrouter"]["fp"] == "fp1"

    def test_does_not_clobber_other_entries(self, tmp_path, monkeypatch):
        """Concurrent writes to different providers don't lose entries."""
        import qubit_cli.models as mod

        cache_path = tmp_path / "provider_models_cache.json"
        monkeypatch.setattr(mod, "_provider_models_cache_path", lambda: cache_path)

        # Seed with one entry
        with patch.object(mod, "_credential_fingerprint", return_value="fp_a"):
            mod.update_provider_cache_entry("provider_a", ["a1"])

        # Write a second entry
        with patch.object(mod, "_credential_fingerprint", return_value="fp_b"):
            mod.update_provider_cache_entry("provider_b", ["b1"])

        cache = mod._load_provider_models_cache()
        assert "provider_a" in cache
        assert cache["provider_a"]["models"] == ["a1"]
        assert "provider_b" in cache
        assert cache["provider_b"]["models"] == ["b1"]

    def test_skips_empty_models(self, tmp_path, monkeypatch):
        """Empty model lists are not written to cache."""
        import qubit_cli.models as mod

        cache_path = tmp_path / "provider_models_cache.json"
        monkeypatch.setattr(mod, "_provider_models_cache_path", lambda: cache_path)

        mod.update_provider_cache_entry("empty_provider", [])
        cache = mod._load_provider_models_cache()
        assert "empty_provider" not in cache

    def test_concurrent_writes_no_lost_entries(self, tmp_path, monkeypatch):
        """Multiple threads writing different providers concurrently — all land."""
        import qubit_cli.models as mod
        import concurrent.futures

        cache_path = tmp_path / "provider_models_cache.json"
        monkeypatch.setattr(mod, "_provider_models_cache_path", lambda: cache_path)

        providers = [f"prov_{i}" for i in range(10)]

        with patch.object(mod, "_credential_fingerprint", side_effect=lambda p: f"fp_{p}"):
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                list(executor.map(
                    lambda p: mod.update_provider_cache_entry(p, [f"model_{p}"]),
                    providers,
                ))

        cache = mod._load_provider_models_cache()
        for p in providers:
            assert p in cache, f"{p} was lost in concurrent write"
            assert cache[p]["models"] == [f"model_{p}"]

# ---------------------------------------------------------------------------
# Parallel prefetch (qubit_cli/model_switch.py)
# ---------------------------------------------------------------------------

class TestPrefetchProviderModelsParallel:
    """Verify ``_prefetch_provider_models_parallel`` fetches concurrently."""

    def test_skips_all_fresh_entries(self, monkeypatch):
        """When all cache entries are fresh, no fetch is made."""
        from qubit_cli.model_switch_providers import _prefetch_provider_models_parallel

        fresh_cache = {
            "openrouter": {"fp": "fp", "at": time.time(), "models": ["m1"]},
            "anthropic": {"fp": "fp", "at": time.time(), "models": ["m2"]},
        }

        with patch("qubit_cli.models._load_provider_models_cache", return_value=fresh_cache), \
             patch("qubit_cli.models._credential_fingerprint", return_value="fp"), \
             patch("qubit_cli.models.cached_provider_model_ids") as fetch:
            _prefetch_provider_models_parallel(["openrouter", "anthropic"])

        fetch.assert_not_called()

    def test_fetches_only_stale_entries(self, monkeypatch):
        """Only providers with stale/missing cache entries are fetched."""
        from qubit_cli.model_switch_providers import _prefetch_provider_models_parallel

        cache = {
            "fresh_prov": {"fp": "fp_f", "at": time.time(), "models": ["m1"]},
        }

        fetch_calls = []

        def mock_fetch(slug, force_refresh=False):
            fetch_calls.append(slug)
            return [f"model_{slug}"]

        with patch("qubit_cli.models._load_provider_models_cache", return_value=cache), \
             patch("qubit_cli.models._credential_fingerprint", return_value="fp_f"), \
             patch("qubit_cli.models.cached_provider_model_ids", side_effect=mock_fetch), \
             patch("qubit_cli.models.update_provider_cache_entry"):
            _prefetch_provider_models_parallel(["fresh_prov", "stale_prov"])

        assert "fresh_prov" not in fetch_calls
        assert "stale_prov" in fetch_calls

    def test_fetches_in_parallel(self, monkeypatch):
        """Multiple providers are fetched concurrently, not serially."""
        from qubit_cli.model_switch_providers import _prefetch_provider_models_parallel

        # Track overlap: if serial, no two fetches should overlap in time.
        active = []
        max_concurrent = [0]
        lock = __import__("threading").Lock()

        def mock_fetch(slug, force_refresh=False):
            with lock:
                active.append(slug)
                max_concurrent[0] = max(max_concurrent[0], len(active))
            time.sleep(0.05)  # simulate network latency
            with lock:
                active.remove(slug)
            return [f"model_{slug}"]

        slugs = [f"prov_{i}" for i in range(6)]

        with patch("qubit_cli.models._load_provider_models_cache", return_value={}), \
             patch("qubit_cli.models._credential_fingerprint", return_value="fp"), \
             patch("qubit_cli.models.cached_provider_model_ids", side_effect=mock_fetch), \
             patch("qubit_cli.models.update_provider_cache_entry"):
            _prefetch_provider_models_parallel(slugs)

        assert max_concurrent[0] > 1, "fetches were serial, not parallel"

    def test_swallows_exceptions(self):
        """A failing provider fetch doesn't raise — best-effort."""
        from qubit_cli.model_switch_providers import _prefetch_provider_models_parallel

        def mock_fetch(slug, force_refresh=False):
            raise ConnectionError("simulated network failure")

        with patch("qubit_cli.models._load_provider_models_cache", return_value={}), \
             patch("qubit_cli.models._credential_fingerprint", return_value="fp"), \
             patch("qubit_cli.models.cached_provider_model_ids", side_effect=mock_fetch), \
             patch("qubit_cli.models.update_provider_cache_entry"):
            # Should not raise
            _prefetch_provider_models_parallel(["failing_prov"])

    def test_empty_list_is_noop(self):
        """Empty provider list does nothing."""
        from qubit_cli.model_switch_providers import _prefetch_provider_models_parallel

        with patch("qubit_cli.models.cached_provider_model_ids") as fetch:
            _prefetch_provider_models_parallel([])
        fetch.assert_not_called()

    def test_skips_ttl_expired_entries_the_serial_path_can_still_serve(self):
        """A TTL-expired entry inside the stale-serve window is not prefetched.

        ``cached_provider_model_ids`` returns such an entry from disk right
        away and revalidates on a background thread, so blocking the picker
        on a parallel fetch buys nothing. ``_PROVIDER_MODELS_STALE_SERVE_MAX``
        is far longer than ``_PROVIDER_MODELS_CACHE_TTL``, so this is the
        state every picker open a TTL after the previous one lands in.
        """
        import qubit_cli.models as models_mod
        from qubit_cli.model_switch_providers import _prefetch_provider_models_parallel

        expired = time.time() - models_mod._PROVIDER_MODELS_CACHE_TTL - 60
        cache = {"openrouter": {"fp": "fp", "at": expired, "models": ["m1"]}}

        with patch("qubit_cli.models._load_provider_models_cache", return_value=cache), \
             patch("qubit_cli.models._credential_fingerprint", return_value="fp"), \
             patch("qubit_cli.models.cached_provider_model_ids") as fetch:
            _prefetch_provider_models_parallel(["openrouter"])

        fetch.assert_not_called()

    def test_fetches_curated_fallback_rows_past_their_short_ttl(self):
        """A curated-fallback row is served only for ``_PROVIDER_MODELS_FALLBACK_TTL``
        and never through the stale window, so the serial call blocks on it and the
        parallel prefetch must fetch it."""
        from qubit_cli.model_switch_providers import _prefetch_provider_models_parallel

        cache = {"openrouter": {"fp": "fp", "at": time.time() - 7200, "models": ["m1"],
                                "fallback": True}}
        fetched = []

        def mock_fetch(slug, force_refresh=False):
            fetched.append(slug)
            return ["m1"]

        with patch("qubit_cli.models._load_provider_models_cache", return_value=cache), \
             patch("qubit_cli.models._credential_fingerprint", return_value="fp"), \
             patch("qubit_cli.models.cached_provider_model_ids", side_effect=mock_fetch), \
             patch("qubit_cli.models.update_provider_cache_entry"):
            _prefetch_provider_models_parallel(["openrouter"])

        assert fetched == ["openrouter"]


class TestStoreCacheEntry:
    """Verify ``_store_cache_entry`` writes safely under concurrency."""

    def test_writes_new_entry(self, tmp_path, monkeypatch):
        """A new entry is persisted to the cache file."""
        import qubit_cli.models as mod

        cache_path = tmp_path / "provider_models_cache.json"
        monkeypatch.setattr(mod, "_provider_models_cache_path", lambda: cache_path)

        mod._store_cache_entry("openrouter", mod._cache_entry("fp1", ["m1", "m2"]))

        cache = mod._load_provider_models_cache()
        assert "openrouter" in cache
        assert cache["openrouter"]["models"] == ["m1", "m2"]
        assert cache["openrouter"]["fp"] == "fp1"

    def test_does_not_clobber_other_entries(self, tmp_path, monkeypatch):
        """Concurrent writes to different providers don't lose entries."""
        import qubit_cli.models as mod

        cache_path = tmp_path / "provider_models_cache.json"
        monkeypatch.setattr(mod, "_provider_models_cache_path", lambda: cache_path)

        # Seed with one entry
        mod._store_cache_entry("provider_a", mod._cache_entry("fp_a", ["a1"]))

        # Write a second entry
        mod._store_cache_entry("provider_b", mod._cache_entry("fp_b", ["b1"]))

        cache = mod._load_provider_models_cache()
        assert "provider_a" in cache
        assert cache["provider_a"]["models"] == ["a1"]
        assert "provider_b" in cache
        assert cache["provider_b"]["models"] == ["b1"]

    def test_concurrent_writes_no_lost_entries(self, tmp_path, monkeypatch):
        """Multiple threads writing different providers concurrently — all land."""
        import qubit_cli.models as mod
        import concurrent.futures

        cache_path = tmp_path / "provider_models_cache.json"
        monkeypatch.setattr(mod, "_provider_models_cache_path", lambda: cache_path)

        providers = [f"prov_{i}" for i in range(10)]

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(
                lambda p: mod._store_cache_entry(p, mod._cache_entry(f"fp_{p}", [f"model_{p}"])),
                providers,
            ))

        cache = mod._load_provider_models_cache()
        for p in providers:
            assert p in cache, f"{p} was lost in concurrent write"
            assert cache[p]["models"] == [f"model_{p}"]

    @pytest.mark.parametrize("cleared", [
    ])
    def test_clear_is_not_undone_by_a_concurrent_locked_write(self, tmp_path, monkeypatch, cleared):
        """``--refresh`` clears under the same lock: a writer that loaded the cache before the clear
        must not save its snapshot afterwards and bring the cleared row back."""
        import threading

        import qubit_cli.models as mod
        from qubit_cli.models_cache_lock import write_under_cache_lock

        import qubit_cli.models_cache_lock as cache_lock_module
        from tests.lock_contention import ContentionObservedLock, watch_file_lock_contention
        from tests.waiting import wait_until

        cache_path = tmp_path / "provider_models_cache.json"
        monkeypatch.setattr(mod, "_provider_models_cache_path", lambda: cache_path)
        contended = watch_file_lock_contention(monkeypatch)
        # In-process writers meet the thread lock first, other processes the file lock.
        thread_lock = ContentionObservedLock()
        monkeypatch.setattr(cache_lock_module, "_thread_lock", thread_lock)
        mod._save_provider_models_cache({
            "openrouter": mod._cache_entry("fp", ["or/model"]),
            "anthropic": mod._cache_entry("fp", ["claude"]),
        })
        loaded = threading.Event()
        release = threading.Event()

        def slow_write():  # _store_cache_entry's write, paused between its read and its save
            cache = mod._load_provider_models_cache()
            loaded.set()
            release.wait(5)
            cache["anthropic"] = mod._cache_entry("fp", ["claude-new"])
            mod._save_provider_models_cache(cache)

        writer = threading.Thread(target=write_under_cache_lock, args=(cache_path, slow_write))
        writer.start()
        assert loaded.wait(5)
        clearer = threading.Thread(target=mod.clear_provider_models_cache, args=(cleared,))
        clearer.start()
        wait_until(lambda: not clearer.is_alive() or contended.is_set() or thread_lock.contended.is_set(),
                   what="the clear finished or blocked on the cache lock")
        release.set()
        writer.join(5)
        clearer.join(5)

        assert "openrouter" not in mod._load_provider_models_cache()
