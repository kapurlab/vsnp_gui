# vSNP Icon Options

You now have **3 professional icon designs** to choose from!

## Icon 1: Original DNA Helix ✓ (Currently Active)
**File:** `icon.png`

**Style:**
- Clean, minimalist DNA double helix
- White helix strands with light teal connecting bars
- Golden SNP marker dots (4 positioned along helix)
- "vSNP" text in white at bottom
- Solid teal background

**Best for:**
- Professional, classic look
- Easy recognition at small sizes
- Traditional scientific aesthetic

---

## Icon 2: DNA Sequence Alignment
**File:** `icon_sequence.png`

**Style:**
- Colored nucleotide sequences (A=red, C=blue, G=yellow, T=green)
- Multiple aligned sequences showing genomic data
- Highlighted SNP with glowing effect (the G with yellow glow in row 4)
- "vSNP" text in white at bottom
- Darker teal gradient background
- Monospaced font showing actual DNA data

**Best for:**
- Immediate recognition of bioinformatics purpose
- Shows actual sequence alignment (what vSNP does)
- Eye-catching with the colored nucleotides
- Appeals to genomics researchers

**Inspired by:** The reference image you showed with colorful ATCG sequences

---

## Icon 3: Glossy 3D Helix
**File:** `icon_glossy.png`

**Style:**
- Modern 3D DNA helix with depth and shading
- Glossy golden SNP markers with shine effects
- Light gradient background (darker at edges, lighter in center)
- Smooth, polished strands with highlights
- "vSNP" text in white at bottom
- Contemporary app icon aesthetic

**Best for:**
- Modern, polished look
- Stands out in macOS Dock
- Professional but approachable
- 3D depth makes it pop visually

**Inspired by:** The reference image with the 3D glossy helix

---

## How to Switch Icons

### Easy Way (Interactive):
```bash
./switch_icon.sh
```
Then choose option 1, 2, or 3!

### Manual Way:
```bash
# For Sequence Alignment icon:
cp icon_sequence.png icon_active.png
./switch_icon.sh  # (choose option 2)

# For Glossy 3D icon:
cp icon_glossy.png icon_active.png
./switch_icon.sh  # (choose option 3)
```

---

## My Recommendations

### If you want **maximum scientific credibility:**
→ Use **Icon 2 (Sequence Alignment)**
- Immediately recognizable as genomics software
- The colored ATCG letters are universally understood by your target audience
- The SNP highlight directly shows what the software does

### If you want **modern, polished appeal:**
→ Use **Icon 3 (Glossy 3D)**
- Looks great in macOS Dock
- Modern aesthetic while still being scientific
- The glossy SNP markers are eye-catching

### If you want **classic simplicity:**
→ Keep **Icon 1 (Original)**
- Clean, won't look dated
- Works at all sizes
- Professional and straightforward

---

## Current Status

✓ **Icon 1 (Original)** is currently active in `vSNP.app`

All icons are ready to use - just run `./switch_icon.sh` to change!

---

## Technical Notes

- All icons are 1024×1024 high resolution
- All have rounded corners (macOS standard)
- All use your teal theme colors
- All include the "vSNP" branding
- All have been optimized for both Retina and non-Retina displays
