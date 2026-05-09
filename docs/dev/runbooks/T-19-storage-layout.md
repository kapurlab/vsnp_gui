# T-19 Storage Layout — Runbook

**Status**: draft, awaiting schedule
**Estimated downtime**: ~60–90 min (tight) / 2 h (with margin)
**Operator**: needs `sudo` on `kapurlab-wgs3`
**Prerequisites**: nobody actively using OOD; communicate window in advance

## Goal

Move the lab off the 932 GB system disk (where everything currently lives) onto the four idle disks. End state:

| Mount | Device | Size | FS | Mount opts |
|---|---|---|---|---|
| `/` | `nvme0n1p3` | 932 GB | ext4 | (existing — untouched) |
| `/home` | `nvme2n1` | 3.7 TB | XFS | `defaults,usrquota,prjquota` |
| `/srv/kapurlab` | `sda` | 10.9 TB | XFS | `defaults,prjquota` |
| `/srv/kapurlab/backup` | `sdb` | 5.5 TB | XFS | `defaults,prjquota` |
| swap | `nvme1n1` | 953 GB | swap | (replaces `/swapfile`) |

Quotas enabled at mkfs/mount time; populating defaults is T-12a's job.

## Pre-flight (do day-before)

```bash
# 1. Confirm devices haven't changed
sudo lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,WWN,MODEL

# Expected (WWN values are the canary if anything was reseated):
# sda     10.9T  ST12000NM000J         WWN 0x5000c500ea7f6693
# sdb      5.5T  TOSHIBA MG10ADP6      WWN 0x5000039de8daf7e5
# nvme1n1  953G  SKHynix HFS001TEJ9X
# nvme2n1  3.7T  WD SN8000S

# 2. Confirm XFS tooling
which mkfs.xfs xfs_quota
modinfo xfs | head -3

# 3. Confirm /home size and contents
sudo du -sh /home
sudo du -sh /home/*

# 4. Confirm no in-flight OOD sessions
ss -tlnp | grep python    # any listening uvicorn = active session
who                       # logged-in users

# 5. Communicate maintenance window
#    "wgs3 down for storage migration HH:MM–HH:MM PT, no impact to pre-staged data"
```

If any active session shows up, drain it before scheduling. 29 stale session dirs at recon time were unrelated (just historical output dirs in `/home/vxk1/ondemand/`).

## Maintenance window

### Step 0 — Quiesce (5 min)

```bash
# Stop OOD's Apache front
sudo systemctl stop apache2

# Confirm no uvicorn / OOD-spawned python listening
ss -tlnp | grep python || echo "clean"

# Optional: kill any stale OOD-spawned processes
sudo pkill -u "$(whoami)" -f "uvicorn app.main:app" 2>/dev/null || true
```

Apache stopped → OOD portal returns connection errors to anyone trying. Acceptable for the window.

### Step 1 — Snapshot of /home onto / (5 min)

Belt-and-suspenders. /home is 18 GB today; tar+zstd to `/root/home-pre-T19.tar.zst` lives on the system disk and is independent of any mistake we make on the data disks.

```bash
sudo tar --zstd -cf /root/home-pre-T19.tar.zst -C / home
sudo ls -lh /root/home-pre-T19.tar.zst
# Expect ~5–10 GB compressed
```

Rollback at any point before fstab edits: `sudo tar --zstd -xf /root/home-pre-T19.tar.zst -C /` (after unmounting any new /home).

### Step 2 — Partition + format (15 min)

Plain GPT, single partition per disk. mkfs.xfs with quotas enabled at format time.

```bash
# 2a. /srv/kapurlab on sda
sudo parted -s /dev/sda mklabel gpt mkpart primary 0% 100%
sudo mkfs.xfs -f -L kapurlab /dev/sda1

# 2b. /srv/kapurlab/backup on sdb
sudo parted -s /dev/sdb mklabel gpt mkpart primary 0% 100%
sudo mkfs.xfs -f -L kapurlab-bk /dev/sdb1

# 2c. /home on nvme2n1
sudo parted -s /dev/nvme2n1 mklabel gpt mkpart primary 0% 100%
sudo mkfs.xfs -f -L home /dev/nvme2n1p1

# 2d. swap on nvme1n1
sudo parted -s /dev/nvme1n1 mklabel gpt mkpart primary linux-swap 0% 100%
sudo mkswap -L swap-nvme1 /dev/nvme1n1p1

# 2e. capture UUIDs for fstab
sudo blkid /dev/sda1 /dev/sdb1 /dev/nvme2n1p1 /dev/nvme1n1p1
```

Save the four UUIDs — we paste them into fstab.

### Step 3 — Migrate /home (20 min for 18 GB, scales linearly)

```bash
# 3a. Mount the new home temporarily under /mnt/newhome
sudo mkdir -p /mnt/newhome
sudo mount -o defaults,usrquota,prjquota /dev/nvme2n1p1 /mnt/newhome

# 3b. rsync /home into it; preserve everything (-aHAX), verbose for log
sudo rsync -aHAXv --info=progress2 /home/ /mnt/newhome/
# Expect ~5 minutes for 18 GB on this hardware

# 3c. Spot-check
sudo du -sh /home /mnt/newhome
ls -la /mnt/newhome/vxk1/ | head

# 3d. Unmount and remount over the actual /home so it eclipses /
sudo umount /mnt/newhome
sudo mount -o defaults,usrquota,prjquota /dev/nvme2n1p1 /home

# 3e. Verify
df -h /home
ls -la /home/vxk1/ | head
```

If 3c shows a mismatch in `du -sh`: do not proceed. Re-run rsync; rollback via tar at Step 1 if persistent.

### Step 4 — Create /srv/kapurlab tree (10 min)

```bash
# 4a. Mount kapurlab on /srv/kapurlab.
#     /srv currently has some stray .aaa.* files at root; we leave them alone
#     (the new mount eclipses them; they're recoverable later if needed).
sudo mkdir -p /srv/kapurlab
sudo mount -o defaults,prjquota /dev/sda1 /srv/kapurlab

# 4b. Skeleton dirs (ownership/perms set in T-11 / T-12a; placeholders here)
sudo mkdir -p /srv/kapurlab/{refs/{vsnp3/{reference_options,vcf_db_folders},kraken,mhc/{pending,bola_current,hla_current}},projects,tools,audit,backup}

# 4c. Mount backup on /srv/kapurlab/backup
sudo mount -o defaults,prjquota /dev/sdb1 /srv/kapurlab/backup

# 4d. Verify
df -h /srv/kapurlab /srv/kapurlab/backup
```

### Step 5 — Swap migration (5 min)

```bash
# 5a. Activate new swap
sudo swapon /dev/nvme1n1p1
swapon --show

# 5b. Disable old /swapfile
sudo swapoff /swapfile
swapon --show
# (we'll remove /swapfile from / and from fstab after reboot verification)
```

### Step 6 — Persist mounts (5 min)

```bash
# 6a. Capture UUIDs (re-fetch in case anything changed)
SDA_UUID=$(sudo blkid -s UUID -o value /dev/sda1)
SDB_UUID=$(sudo blkid -s UUID -o value /dev/sdb1)
HOME_UUID=$(sudo blkid -s UUID -o value /dev/nvme2n1p1)
SWAP_UUID=$(sudo blkid -s UUID -o value /dev/nvme1n1p1)

# 6b. Append to /etc/fstab (review first!)
sudo tee -a /etc/fstab <<EOF

# T-19 storage layout (added $(date +%Y-%m-%d))
UUID=$HOME_UUID   /home                  xfs  defaults,usrquota,prjquota  0  2
UUID=$SDA_UUID    /srv/kapurlab          xfs  defaults,prjquota           0  2
UUID=$SDB_UUID    /srv/kapurlab/backup   xfs  defaults,prjquota           0  2
UUID=$SWAP_UUID   none                   swap sw                          0  0
EOF

# 6c. Sanity check (parses fstab without mounting)
sudo findmnt --verify

# 6d. Test by remounting
sudo umount /srv/kapurlab/backup /srv/kapurlab /home
sudo mount -a
df -h /home /srv/kapurlab /srv/kapurlab/backup
```

`findmnt --verify` flags malformed fstab entries before they brick reboot.

### Step 7 — Bring services back (5 min)

```bash
# Restart Apache (OOD front)
sudo systemctl start apache2
sudo systemctl status apache2 --no-pager | head -5

# Spot-check OOD reachability
curl -sI http://localhost/ | head -1
```

User can launch a fresh OOD session at this point. If session works end-to-end, we're done with the live operation.

### Step 8 — Verify reboot persistence (10 min)

Most likely catch-point for fstab typos. Schedule a controlled reboot before declaring done.

```bash
sudo reboot
# wait 60s, ssh back in
```

After SSH returns:

```bash
df -h /home /srv/kapurlab /srv/kapurlab/backup
swapon --show
sudo systemctl status apache2 --no-pager | head -5
```

All three mounts present; swap on nvme1n1p1; Apache up. Done.

## Post-migration cleanup (next-day)

```bash
# After 24–48h of stable operation:
# 1. Remove the legacy swapfile from /
sudo rm /swapfile
sudo sed -i '/^\/swapfile/d' /etc/fstab

# 2. Remove the pre-migration tar
sudo rm /root/home-pre-T19.tar.zst

# 3. The original /home contents are now SHADOWED by the new mount.
#    The bytes on / are not freed. To reclaim the ~18 GB:
sudo umount /home               # exposes the underlying / copy
sudo rm -rf /home/*             # removes the shadowed copy
sudo mount /home                # re-mount the real /home
```

Keep the legacy /home shadow copy at least 1 week before the cleanup unmount/rm.

## Quota setup (separate, post-T-19)

This belongs to T-12a, but the foundation is here. Quotas were enabled at mount time; they're inactive until project IDs and limits are assigned. T-12a will:

- Define project IDs in `/etc/projects` and `/etc/projid`
- Run `xfs_quota -x -c "project -s <name>" /srv/kapurlab` for each project subdir
- Set soft/hard limits with `xfs_quota -x -c "limit -p bsoft=5T bhard=7T <name>"`
- Set per-user defaults on /home with `xfs_quota -x -c "limit bsoft=1T bhard=1.5T <user>" /home`

## Rollback

| Failure point | Action |
|---|---|
| Step 2 (mkfs fails) | Disks still raw. Investigate, retry. Apache restart only. |
| Step 3 (rsync mismatch) | `umount /home` (if mounted) → re-rsync OR untar from Step 1 backup. |
| Step 6 (mount -a fails) | `sudo nano /etc/fstab` to fix; if can't, `sudo mount -o ro,remount /` for safety; restore from `findmnt --verify` output. |
| Step 8 (reboot fails) | Boot to recovery; `mount -o remount,rw /`; comment out the new fstab lines; reboot. /home contents recover from `/root/home-pre-T19.tar.zst`. |

## Open questions (decide before scheduling)

1. **Window time**: when's least disruptive for Tod / Ro / future onboarders? Suggest a 90-min slot on a weekday morning.
2. **Backup of /root/home-pre-T19.tar.zst**: copy to a second location (your Mac via scp?) before starting, just in case the system disk itself has issues during the window?
3. **Kernel/firmware updates**: Ubuntu 24.04 is current. Worth applying pending updates in the same window so the post-migration reboot is also a "fresh" reboot?
