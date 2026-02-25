# Echo Mini Firmware Patch

Tools for customizing the Echo Mini (RKnano DAP) firmware `.IMG` file.

## Scripts

| Script | Purpose |
|--------|---------|
| `patch_echo_mini.py` | Per-theme boot/shutdown animation patch |
| `fix_img_echo_mini.py` | Repair a `.IMG` rejected by the Echo Mini during update |

---

## patch_echo_mini.py — Per-theme boot patch

### What it does

By default, regardless of the active visual theme (A–E), the Echo Mini always
plays the same boot/shutdown animation (Theme A's). This patch modifies the
firmware so each theme has its own independent set of boot/shutdown images.

### Changes applied

| # | What | Detail |
|---|------|--------|
| 1 | Expands ROCK26 table | 1602 → 1870 entries (5 blocks × 374) |
| 2 | Expands metadata table | Boot entries duplicated × 5, renamed `T_X_*` |
| 3 | Patches CMP instruction | `CMP R0,#0x43` → `CMP R0,#0x00` — disables forced jump to Theme A |
| 4 | Patches 4 ADDW instructions | Block offsets: `307×N` → `374×N` |
| 5 | Updates `fw_end` and header copy | `_fix_integrity()` ensures the bootloader finds the header |

> **Note on CRC32:** The Echo Mini includes an RKnano trailer with CRC32, but
> **does not actively verify it** during flashing. The patch intentionally
> preserves the original trailer without recalculating it.

### Usage

```bash
# Apply the patch (generates HIFIEC20_patched.IMG)
python patch_echo_mini.py HIFIEC20.IMG

# Save with a custom name
python patch_echo_mini.py HIFIEC20.IMG -o firmware_patched.IMG

# Check if the patch is already applied
python patch_echo_mini.py HIFIEC20.IMG --check
```

### Example output

```
Loading HIFIEC20.IMG ...
  Resources : 1602
  Block size: 307 resources/theme
  CMP offset: 0x3DF5A  (value: 0x2843)
  ADDW      : [307, 614, 921, 1228]
  Status    : not patched

Backup saved to: HIFIEC20.IMG.bak

Applying patch ...
  [████████████████████] 100%

Patch applied successfully
   CMP  : 0x3DF5A  (0x2843 → 0x2800)
   ADDW : [307, 614, 921, 1228] → [374, 748, 1122, 1496]
   Table: 1602 → 1870 entries
   Block: 307 → 374 resources/theme

Saved to: HIFIEC20_patched.IMG
```

---

## fix_img_echo_mini.py — Repair a rejected .IMG

### When to use it

Use this when the Echo Mini detects the update file but cancels it within
1-3 seconds. This happens when:
- The `.IMG` was patched/modified but `fw_end` points outside the file bounds
- The trailer is corrupted (e.g. `c618c618`)
- The 512-byte header copy at `fw_end` is missing

### How it works

```
Echo Mini bootloader reads fw_end from header[0x1F4]
        │
        ▼
Looks for 512-byte header copy at offset fw_end
        │
   Is it inside the file?
        │ NO  → cancels update, deletes the file  ✗  (~1-3 sec)
        │ YES → proceeds with flashing            ✓
```

### Fixes applied

1. Recalculates `fw_end` from the actual end of Part5
2. Extends the file so `fw_end` is within bounds
3. Writes the 512-byte header copy at `fw_end`
4. Preserves the original trailer (CRC not verified by device)

### Usage

```bash
# Repair (generates firmware_fixed.IMG)
python fix_img_echo_mini.py firmware.IMG

# Save with a custom name
python fix_img_echo_mini.py firmware.IMG -o firmware_repaired.IMG

# Show file info without repairing
python fix_img_echo_mini.py firmware.IMG --info
```

### Example output

```
Loading HIFIEC20_boots.IMG ...
  Size       : 55,728,744 bytes
  fw_end     : 0x3530000  (OUTSIDE file — will be rejected)
  Part5 end  : 0x3530000
  Header copy: missing
  Trailer    : c618c618

Backup saved to: HIFIEC20_boots.IMG.bak

Firmware integrity fixed

   fw_end     : 0x3530000 → 0x3530000
   Size       : 55,728,744 → 56,623,108 bytes (+868 KB)
   Header copy: written at fw_end
   Trailer    : c618c618 → c618c618

Saved to: HIFIEC20_boots_fixed.IMG
```

---

## Patch flow diagram

```
ORIGINAL FIRMWARE (.IMG, 32 MB)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
0x000000  ┌────────────────────┐
          │   HEADER 512 B     │  magic: RKnanoFW
          │   fw_end=0x1FA0000 │
0x000200  ├────────────────────┤
          │   LOADER (ARM)     │
          │  ┌──────────────┐  │
          │  │ CMP R0,#0x43 │  │ ← offset 0x3DF5A
          │  │ ADDW #307    │  │ ← one per theme B/C/D/E
          │  │ ADDW #614    │  │
          │  │ ADDW #921    │  │
          │  │ ADDW #1228   │  │
          │  └──────────────┘  │
0x9B5998  ├────────────────────┤
          │   PART5 (resources)│
          │  ROCK26: 1602 entries
          │  Metadata: 1602 entries
0x1FA0000 ├────────────────────┤ ← fw_end
          │   HEADER COPY      │
0x2000000 ├────────────────────┤
          │   3d94a194         │  trailer (4 bytes)
0x2000004 └────────────────────┘


PATCHED FIRMWARE (.IMG, 54 MB)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
0x000200  ├────────────────────┤
          │   LOADER (ARM)     │
          │  ┌──────────────┐  │
          │  │ CMP R0,#0x00 │  │ ← PATCHED ✓
          │  │ ADDW #374    │  │ ← PATCHED ✓
          │  │ ADDW #748    │  │
          │  │ ADDW #1122   │  │
          │  │ ADDW #1496   │  │
          │  └──────────────┘  │
0x9B5998  ├────────────────────┤
          │   PART5 (resources)│
          │  ROCK26: 1870 entries ✓
          │  Metadata: 1870 entries ✓
          │  Block A [0..373]:   original boots + A themed
          │  Block B [374..747]: T_B_* boots  + B themed
          │  Block C [748..1121]:T_C_* boots  + C themed
          │  Block D [1122..1495]:T_D_* boots + D themed
          │  Block E [1496..1869]:T_E_* boots + E themed
0x3530000 ├────────────────────┤ ← fw_end (updated) ✓
          │   HEADER COPY      │ ✓
0x35FFFF4 ├────────────────────┤
          │   3d94a194         │  trailer preserved ✓
0x3600004 └────────────────────┘
```

---

## Requirements

- Python 3.8 or higher
- No external dependencies

## Compatibility

Tested with **Echo Mini v3.2.0** firmware (`HIFIEC20.IMG`).  
Offsets are auto-detected — no manual configuration needed.
