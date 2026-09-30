# Linux I/O Performance Diagnostics — System Freezes During Heavy Writes

## When to Use This Reference

When the user reports that programs crash, freeze, or become unresponsive during:
- Steam game updates / downloads
- System updates (`apt upgrade`, etc.)
- Large file transfers or copies
- Any write-heavy workload

The core symptom: **system works fine at idle, but everything locks up when writing to disk.**

## Diagnostic Protocol

### 1. Measure Actual I/O Pressure (PSI — Pressure Stall Information)

This is the single most important metric. Skip `top`/`htop` for I/O; check PSI directly:

```bash
cat /proc/pressure/io
```

Read the output:
- **`some avg10`** — % of time at least one task was stalled on I/O
- **`full avg10`** — % of time ALL CPUs were idle, waiting for I/O (complete freeze)

**Thresholds:**
- `some > 30` = moderate problem
- `some > 60` = severe
- `full > 40` = system is freezing for substantial periods
- `full > 70` = catastrophic (what Tricky's system showed: 78% full)

Also check CPU iowait with `iostat -x 1 3` and look at `%iowait`.

### 2. Check System Memory and Dirty Page Budget

```bash
free -h                          # Total RAM
sysctl vm.dirty_ratio vm.dirty_background_ratio
```

**The critical calculation:**
- `dirty_ratio` = % of total RAM allowed to be dirty before processes block
- On a 62GB machine with `dirty_ratio=20`: **12.4GB of pending writes** before the hard block
- `dirty_background_ratio=10`: background flush starts at **6.2GB**

**This is the #1 cause of "everything freezes during updates" on high-RAM systems.** The defaults were designed for 1-4GB machines. On 64GB+ systems they cause catastrophic write-back spikes.

### 3. Check Storage Stack Depth

List the I/O path layers:

```bash
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE
mount | grep " / "
```

**Multi-layer stacks amplify the problem:**
```
Game → ext4 → LVM → LUKS (dm_crypt) → SATA SSD
```

Each layer adds queuing and overhead. Compare write latency at each layer:

```bash
iostat -x 1 3   # Watch w_await (write latency in milliseconds)
```

**Red flag:** When `w_await` on the LVM/dm device (e.g., dm-1) is 100x higher than the physical device (sda) — this confirms a massive queuing backlog at the filesystem/LVM layer.

### 4. Check for Hung Tasks

```bash
journalctl -b --no-pager | grep "blocked for more than"
```

Any task blocked > 120 seconds is a kernel hung-task warning and confirms severe I/O saturation.

### 5. Check Disk Space

```bash
df -h /
```

**Rule of thumb:** If < 20% free, any large update that needs temp space (Steam, apt, etc.) compounds I/O contention. Look for abandoned large files:
- Old backup directories (`*-backup-*`, `*reinstall*`)
- Unused snap packages (`snap list`)
- `/var/log/` archives

### 6. Check for Known Application I/O Bugs

**Steam for Linux — Valve Issue #6073** (open since 2019):
- Steam spawns 50+ threads all writing synchronously during updates
- Uses `fsync`/`O_SYNC` writes that bypass the page cache
- Runs at nice -10 (high priority), starving other processes when it blocks
- No official fix from Valve — must work around at the kernel level

## Fixes (ranked by impact)

### Fix 1: Lower VM Dirty Ratios (HIGHEST IMPACT)

Set absolute caps via bytes, not percentages, for predictable behaviour regardless of RAM size:

```bash
# Live (immediate)
sudo sysctl -w vm.dirty_ratio=5
sudo sysctl -w vm.dirty_background_ratio=2
sudo sysctl -w vm.swappiness=10

# Persistent (survive reboot) — add to /etc/sysctl.conf or /etc/sysctl.d/*.conf
echo 'vm.dirty_ratio = 5
vm.dirty_background_ratio = 2
vm.swappiness = 10' | sudo tee -a /etc/sysctl.conf
```

**What these do:**
- `dirty_ratio=5` → max ~3.1GB dirty on 62GB (vs 12.4GB default) — shorter, more frequent flushes
- `dirty_background_ratio=2` → background starts at ~1.24GB (vs 6.2GB default)
- `swappiness=10` → with plenty of RAM, don't touch swap unless truly necessary

**Expected improvement:** Dramatic. Write bursts are smaller and the system stays responsive.

### Fix 2: Enable `noatime` on Root Filesystem

Every file read updates the access time metadata, adding write traffic. Disable it:

```bash
# Edit /etc/fstab — change "defaults" to "defaults,noatime" on the / line
# Then remount:
sudo mount -o remount,noatime /
```

### Fix 3: Free Disk Space

Remove unnecessary large files to keep headroom above 30% free. Common candidates:
- Pre-reinstall backup directories (`*backup*`, `*reinstall*`)
- Old snap revisions (`snap list --all` + `sudo snap remove <name> --revision N`)
- Unused Docker images (`docker image prune -a`)

### Fix 4: Isolate Write-Heavy Workloads to Separate Drive

If a second SSD is available (even a smaller 120GB), wipe it and use as a dedicated Steam/game/scratch drive:
- Prevents game I/O from competing with system I/O
- Keeps the root filesystem responsive during Steam updates

### Fix 5: Wrap Steam with `eatmydata` (Optional)

Suppresses `fsync` calls from Steam, preventing it from bypassing the page cache:

```bash
sudo apt install eatmydata
# Edit Steam desktop entry to prefix with `eatmydata`
# Or launch from terminal: eatmydata steam
```

**Trade-off:** Small risk of data loss on power failure mid-write. Acceptable for gaming data (re-downloadable), not for critical work.

## Verification

After applying fixes, confirm:

```bash
# Live values
sysctl vm.dirty_ratio vm.dirty_background_ratio vm.swappiness

# Mount options
mount | grep " / "

# Free space
df -h /

# PSI improvement (should be significantly lower under the same workload)
cat /proc/pressure/io
```

## Root Cause Summary

The user's system had a perfect storm:
1. **62GB RAM × 20% dirty_ratio** = 12.4GB write-back backlog
2. **Triple-stacked I/O path** (ext4 → LVM → LUKS → SATA SSD) amplifying latency
3. **Steam's synchronous write bug** (50+ threads, fsync, nice -10) as trigger
4. **Disk at 80% capacity** — no room for temp files
5. **63GB orphaned backup** consuming critical headroom

Result: PSI I/O full at 78%, write latencies of 4+ seconds, load averages of 37+ on a 16-core CPU, all from I/O wait.
