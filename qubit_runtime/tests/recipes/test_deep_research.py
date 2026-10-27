import argparse
import io
import json
from contextlib import redirect_stdout
from datetime import datetime, timezone

import pytest

from agent.runtime_port import RuntimeCapabilities, TurnEvent
from qubit_cli import recipe_cmd
from qubit_cli.recipe_cmd import cmd_recipe
from recipes import deep_research
from recipes.deep_research import (
    DEEP_RESEARCH,
    collect_sections,
    make_collectors,
)
from recipes.registry import build_recipe_registry
from recipes.runner import RecipeInputError, build_prompt, run_recipe
from recipes.sections import available

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


class _FakeBackend:
    name = "fake"
    capabilities = RuntimeCapabilities(True, True, False, False)

    def __init__(self, events=()):
        self.events = events
        self.requests = []

    def run_turn(self, request, cancel=None):
        self.requests.append(request)
        yield from self.events


class _Registry:
    def __init__(self, backends):
        self._backends = backends

    def get(self, name):
        return self._backends[name]


def _search(*rows):
    return lambda query, limit: {
        "success": True,
        "data": {"web": list(rows)},
    }


def _fetch(*rows):
    return lambda urls: {"results": list(rows)}


def _by_id(results):
    return {result.id: result for result in results}


def _collect(topic="quantum error correction", *, search=None, fetch=None,
             max_results=8, max_sources=5):
    collectors = make_collectors(
        topic, search_fn=search, fetch_fn=fetch,
        max_results=max_results, max_sources=max_sources)
    return collectors


def test_spec_declares_the_bounded_input_driven_surface():
    spec = DEEP_RESEARCH
    assert spec.id == "deep_research"
    assert spec.sections == ("search", "sources")
    assert spec.input_fields == ("topic",)
    assert spec.default_schedule == "on_demand"
    assert spec.approval_policy == "read_only_collectors"
    assert spec.output_format == "cited_research_brief"
    assert spec.required_tools == ("web_search", "web_extract")
    assert "deep research" in spec.voice_aliases
    assert spec.as_dict()["inputs"] == ["topic"]


def test_registry_includes_deep_research():
    registry = build_recipe_registry()
    assert registry.get("deep_research") is DEEP_RESEARCH


def test_search_dedupes_numbers_and_bounds_results():
    search = _search(
        {"title": "A", "url": "https://example.com/a", "description": "one"},
        {"title": "A again", "url": "https://example.com/a/", "description": "dup"},
        {"title": "B", "url": "https://example.com/b"},
        {"title": "C", "url": "https://example.com/c"},
        {"title": "bad", "url": "javascript:alert(1)"},
    )
    results = collect_sections(
        {"topic": "t"}, search_fn=search, fetch_fn=lambda urls: {"results": []},
        now=NOW)
    by_id = _by_id(results)
    search_result = by_id["search"]
    assert search_result.available
    assert search_result.facts[0].startswith("[1] A — https://example.com/a")
    assert "[2] B — https://example.com/b" in search_result.facts[1]
    cited = [f for f in search_result.facts if f.startswith("[")]
    assert len(cited) == 3
    assert cited[2].startswith("[3] C — https://example.com/c")

    bounded = collect_sections(
        {"topic": "t"}, search_fn=_search(
            *[{"title": str(i), "url": f"https://example.com/{i}"} for i in range(10)]),
        fetch_fn=lambda urls: {"results": []}, now=NOW)
    numbers = [f.split("]")[0] + "]" for f in _by_id(bounded)["search"].facts
                if f.startswith("[")]
    assert numbers == [f"[{i}]" for i in range(1, 9)]  # max_results bounds the search
    assert any("Showing the first 8 of 10 results." in f
               for f in _by_id(bounded)["search"].facts)


def test_missing_provider_is_a_typed_connector_gap():
    results = collect_sections(
        {"topic": "t"},
        search_fn=lambda q, limit: {
            "success": False,
            "error": "No web search provider configured. Run `qubit tools` to set one up."},
        fetch_fn=lambda urls: {"results": []}, now=NOW)
    result = _by_id(results)["search"]
    assert not result.available
    assert result.reason == "connector_not_configured"


def test_misconfigured_provider_is_a_connector_gap():
    from tools.web_tools_extract import _disabled_plugin_error, _strict_selection_error

    for text in (
        _strict_selection_error("extract", "bogus"),
        _disabled_plugin_error("extract", "web/firecrawl"),
    ):
        results = collect_sections(
            {"topic": "t"},
            search_fn=lambda q, limit, text=text: {"success": False, "error": text},
            fetch_fn=lambda urls: {"results": []}, now=NOW)
        assert _by_id(results)["search"].reason == "connector_not_configured", text


def test_missing_credential_is_typed_not_a_tool_error():
    results = collect_sections(
        {"topic": "t"},
        search_fn=lambda q, limit: {
            "success": False, "error": "TAVILY_API_KEY is not set"},
        fetch_fn=lambda urls: {"results": []}, now=NOW)
    result = _by_id(results)["search"]
    assert result.reason == "missing_credential"


def test_generic_search_failure_is_a_tool_error_and_redacted():
    results = collect_sections(
        {"topic": "t"},
        search_fn=lambda q, limit: {
            "success": False, "error": "boom sk-abcdefghijklmnop"},
        fetch_fn=lambda urls: {"results": []}, now=NOW)
    result = _by_id(results)["search"]
    assert result.reason == "tool_error"
    assert "sk-abcdefghijklmnop" not in result.detail


def test_invalid_json_is_a_typed_tool_error():
    results = collect_sections(
        {"topic": "t"}, search_fn=lambda q, limit: "not json",
        fetch_fn=lambda urls: {"results": []}, now=NOW)
    result = _by_id(results)["search"]
    assert result.reason == "tool_error"
    assert "invalid JSON" in result.detail


def test_error_only_tool_payload_is_a_gap_not_no_results():
    """``tool_error(...)`` is an ``{"error"}`` body with no success key.

    Treating it as an empty list reported "No web results" for a crashed
    provider, hiding the real failure.
    """
    results = collect_sections(
        {"topic": "t"},
        search_fn=lambda q, limit: {"error": "Error searching web: provider crashed"},
        fetch_fn=lambda urls: {"results": []}, now=NOW)
    by_id = _by_id(results)
    assert not by_id["search"].available
    assert by_id["search"].reason == "tool_error"
    assert "provider crashed" in by_id["search"].detail
    assert not by_id["sources"].available
    assert "the search section failed" in by_id["sources"].detail


def test_error_only_fetch_payload_keeps_the_real_detail():
    results = collect_sections(
        {"topic": "t"},
        search_fn=_search({"title": "A", "url": "https://example.com/a"}),
        fetch_fn=lambda urls: {"error": "Error extracting content: gateway timeout"},
        now=NOW)
    sources = _by_id(results)["sources"]
    assert not sources.available
    assert sources.reason == "tool_error"
    assert "gateway timeout" in sources.detail
    assert "not returned by the extract backend" not in sources.detail


def test_fetch_excerpts_keep_the_search_numbering():
    search = _search(
        {"title": "A", "url": "https://example.com/a"},
        {"title": "B", "url": "https://example.com/b"},
        {"title": "C", "url": "https://example.com/c"},
    )
    fetch = _fetch(
        {"url": "https://example.com/a", "title": "A",
         "content": "The  result   is 42."},
        {"url": "https://example.com/b", "error": "timeout after 10s"},
    )
    results = collect_sections({"topic": "t"}, search_fn=search, fetch_fn=fetch,
                               max_sources=2, now=NOW)
    sources = _by_id(results)["sources"]
    assert sources.available
    assert "[1] A — https://example.com/a — excerpt: The result is 42." in sources.facts[0]
    assert "[2] B — https://example.com/b — content unavailable (timeout after 10s)" in sources.facts[1]
    assert "[3] C — https://example.com/c — not fetched (source budget reached)" in sources.facts[2]


def test_fetch_budget_caps_extraction_without_dropping_citations():
    search = _search(*[
        {"title": f"S{i}", "url": f"https://example.com/{i}"} for i in range(1, 7)])
    seen = []

    def fetch(urls):
        seen.extend(urls)
        return {"results": []}

    results = collect_sections({"topic": "t"}, search_fn=search, fetch_fn=fetch,
                               now=NOW, sections=("search", "sources"))
    assert len(seen) == 5
    assert seen == [f"https://example.com/{i}" for i in range(1, 6)]
    assert not _by_id(results)["sources"].available  # every fetch returned nothing
    assert _by_id(results)["sources"].reason == "tool_error"


def test_redirected_row_maps_by_source_url_not_position():
    """A row whose ``url`` is the redirect target must attach to the requested
    URL named in ``metadata.sourceURL``; positional fallback previously gave
    page B's text to citation A."""
    results = collect_sections(
        {"topic": "t"},
        search_fn=_search(
            {"title": "A", "url": "https://example.com/a"},
            {"title": "B", "url": "https://example.com/b"}),
        fetch_fn=lambda urls: {"results": [{
            "url": "https://redirect.example/x",
            "metadata": {"sourceURL": "https://example.com/b"},
            "content": "B text",
        }]},
        now=NOW)
    facts = _by_id(results)["sources"].facts
    assert "[1] A — https://example.com/a — content unavailable" in facts[0]
    assert "[2] B — https://example.com/b — excerpt: B text" in facts[1]


def test_url_less_row_still_uses_the_request_order_fallback():
    results = collect_sections(
        {"topic": "t"},
        search_fn=_search({"title": "A", "url": "https://example.com/a"}),
        fetch_fn=lambda urls: {"results": [{"content": "positional text"}]},
        now=NOW)
    assert "excerpt: positional text" in _by_id(results)["sources"].facts[0]


def test_all_fetches_failing_is_a_typed_tool_error():
    results = collect_sections(
        {"topic": "t"},
        search_fn=_search({"title": "A", "url": "https://example.com/a"}),
        fetch_fn=_fetch({"url": "https://example.com/a", "error": "connection refused"}),
        now=NOW)
    result = _by_id(results)["sources"]
    assert not result.available
    assert result.reason == "tool_error"
    assert "no source could be fetched" in result.detail


def test_no_results_is_honest_in_both_sections():
    results = collect_sections(
        {"topic": "nothing"}, search_fn=_search(),
        fetch_fn=lambda urls: {"results": []}, now=NOW)
    by_id = _by_id(results)
    assert by_id["search"].available
    assert by_id["search"].facts == ("No web results for 'nothing'.",)
    assert by_id["sources"].available
    assert "search returned no usable URLs" in by_id["sources"].facts[0]


def test_long_url_is_fetched_whole_but_rendered_bounded():
    """A long URL must not be silently truncated into a different (404) target;
    the fact text is bounded by the normal clip instead."""
    long_url = "https://example.com/" + "a" * 400
    seen = []

    def fetch(urls):
        seen.extend(urls)
        return {"results": [{"url": long_url, "content": "ok"}]}

    results = collect_sections(
        {"topic": "t"},
        search_fn=_search({"title": "A", "url": long_url}),
        fetch_fn=fetch, max_sources=1, now=NOW)
    assert seen == [long_url]
    search_fact = _by_id(results)["search"].facts[0]
    assert search_fact.startswith("[1] A — https://example.com/")
    assert len(search_fact) <= 500


def test_sources_without_search_are_a_typed_gap():
    from recipes.sections import SectionUnavailable, result_or_unavailable

    collectors = make_collectors(
        "t", search_fn=_search(), fetch_fn=lambda urls: {"results": []})
    # Calling the sources collector first is the only ordering violation the
    # framework allows; it must refuse rather than invent a fetch plan.
    result = result_or_unavailable("sources", "Sources", collectors["sources"], NOW)
    assert not result.available
    assert result.reason == "tool_error"


def test_prompt_carries_topic_numbered_sources_and_citation_rules():
    results = collect_sections(
        {"topic": "quantum error correction"},
        search_fn=_search({"title": "A", "url": "https://example.com/a"}),
        fetch_fn=_fetch({"url": "https://example.com/a", "content": "surface codes"}),
        now=NOW)
    prompt = build_prompt(DEEP_RESEARCH, results, NOW, {"topic": "quantum error correction"})
    assert "quantum error correction" in prompt
    assert "[1] A — https://example.com/a" in prompt
    assert "excerpt: surface codes" in prompt
    assert "Cite every claim with the bracketed source number" in prompt
    assert 'End with a Sources: section' in prompt


def test_dry_run_is_deterministic_and_calls_no_model(monkeypatch):
    monkeypatch.setattr(
        deep_research, "_default_search",
        _search({"title": "A", "url": "https://example.com/a", "description": "one"}))
    monkeypatch.setattr(
        deep_research, "_default_fetch",
        _fetch({"url": "https://example.com/a", "content": "full text"}))
    backend = _FakeBackend([TurnEvent("done", text="should not be used")])
    registry = _Registry({"fake": backend})

    first = run_recipe(
        DEEP_RESEARCH, registry=registry, engine="fake", dry_run=True,
        inputs={"topic": "a topic"}, now=NOW)
    second = run_recipe(
        DEEP_RESEARCH, registry=registry, engine="fake", dry_run=True,
        inputs={"topic": "a topic"}, now=NOW)

    assert first.exit_code == 0
    assert first.brief == second.brief
    assert first.brief.startswith("# Deep research")
    assert "## Web search" in first.brief and "## Sources" in first.brief
    assert "[1] A — https://example.com/a" in first.brief
    assert backend.requests == []


def test_factory_collectors_feed_the_model_prompt():
    monkeypatch_backend = _FakeBackend([
        TurnEvent("done", text="A cited brief [1]."),
    ])
    collectors = _collect(
        "a topic",
        search=_search({"title": "A", "url": "https://example.com/a"}),
        fetch=_fetch({"url": "https://example.com/a", "content": "evidence"}))
    run = run_recipe(
        DEEP_RESEARCH, registry=_Registry({"fake": monkeypatch_backend}),
        engine="fake", collectors=collectors, inputs={"topic": "a topic"}, now=NOW)
    assert run.exit_code == 0
    assert run.brief == "A cited brief [1]."
    prompt = monkeypatch_backend.requests[0].prompt
    assert "a topic" in prompt
    assert "[1] A — https://example.com/a" in prompt
    assert "excerpt: evidence" in prompt


def test_missing_topic_is_a_typed_input_error():
    with pytest.raises(RecipeInputError) as exc:
        run_recipe(DEEP_RESEARCH, registry=_Registry({"fake": _FakeBackend()}),
                   engine="fake", dry_run=True)
    assert "topic" in str(exc.value)


def test_blank_topic_is_rejected():
    with pytest.raises(RecipeInputError):
        run_recipe(DEEP_RESEARCH, registry=_Registry({"fake": _FakeBackend()}),
                   engine="fake", dry_run=True, inputs={"topic": "   "})


def test_recipe_spec_rejects_reserved_input_names():
    from recipes.spec import RecipeSpec

    with pytest.raises(ValueError):
        RecipeSpec(
            id="bad_input", title="t", description="d", prompt="{facts}",
            sections=("search",), required_capabilities=(), required_tools=(),
            default_schedule="on_demand", approval_policy="read_only_collectors",
            output_format="text", input_fields=("facts",))


# --- CLI ------------------------------------------------------------------


def _args(**kw):
    base = dict(recipe_action="run", recipe_id="deep_research", engine="fake",
                dry_run=False, json=False, topic="")
    base.update(kw)
    return argparse.Namespace(**base)


def test_cli_topic_is_forwarded(monkeypatch):
    captured = {}

    def fake_run(spec, **kw):
        captured.update(kw)
        kw.pop("dry_run", None)
        return run_recipe(spec, dry_run=True, **kw)

    monkeypatch.setattr(
        deep_research, "_default_search",
        _search({"title": "A", "url": "https://example.com/a"}))
    monkeypatch.setattr(
        deep_research, "_default_fetch", _fetch({"url": "https://example.com/a"}))
    monkeypatch.setattr(recipe_cmd, "run_recipe", fake_run)

    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_recipe(_args(topic="the topic"),
                          backend_registry=_Registry({"fake": _FakeBackend()})) == 0
    assert captured["inputs"] == {"topic": "the topic"}
    assert "## Web search" in out.getvalue()


def test_cli_missing_topic_exits_two(capsys):
    assert cmd_recipe(_args(), backend_registry=_Registry({"fake": _FakeBackend()})) == 2
    assert "topic" in capsys.readouterr().err


def test_parser_registers_topic_flag():
    from qubit_cli.subcommands.recipe import build_recipe_parser

    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command")
    build_recipe_parser(subs, cmd_recipe=lambda args: 0)
    args = parser.parse_args(
        ["recipe", "run", "deep_research", "--topic", "quantum", "--dry-run"])
    assert args.topic == "quantum" and args.dry_run


def test_sections_only_contain_available_or_typed_gaps():
    results = collect_sections(
        {"topic": "t"},
        search_fn=_search({"title": "A", "url": "https://example.com/a"}),
        fetch_fn=_fetch({"url": "https://example.com/a", "content": "ok"}),
        now=NOW)
    assert all(result.available or result.reason for result in results)
    assert _by_id(results)["sources"].source == "web extract backend"
