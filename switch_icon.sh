#!/bin/bash

# Script to switch between different vSNP app icons

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "vSNP Icon Switcher"
echo "=================="
echo ""
echo "Available icon styles:"
echo "  1) Original - Simple DNA helix (icon.png)"
echo "  2) Sequence - DNA sequence alignment with SNP highlight (icon_sequence.png)"
echo "  3) Glossy   - 3D glossy DNA helix (icon_glossy.png)"
echo ""
echo -n "Choose an icon (1-3): "
read choice

case $choice in
    1)
        SOURCE="icon.png"
        NAME="Original DNA Helix"
        ;;
    2)
        SOURCE="icon_sequence.png"
        NAME="Sequence Alignment"
        ;;
    3)
        SOURCE="icon_glossy.png"
        NAME="Glossy 3D Helix"
        ;;
    *)
        echo "Invalid choice!"
        exit 1
        ;;
esac

echo ""
echo "Selected: $NAME"
echo "Processing..."

# Create iconset directory
rm -rf icon.iconset
mkdir icon.iconset

# Generate all icon sizes
python3 << EOF
from PIL import Image

img = Image.open('$SOURCE')

sizes = [
    (16, 'icon_16x16.png'),
    (32, 'icon_16x16@2x.png'),
    (32, 'icon_32x32.png'),
    (64, 'icon_32x32@2x.png'),
    (128, 'icon_128x128.png'),
    (256, 'icon_128x128@2x.png'),
    (256, 'icon_256x256.png'),
    (512, 'icon_256x256@2x.png'),
    (512, 'icon_512x512.png'),
    (1024, 'icon_512x512@2x.png'),
]

for size, filename in sizes:
    resized = img.resize((size, size), Image.Resampling.LANCZOS)
    resized.save(f'icon.iconset/{filename}')
EOF

# Convert to icns
iconutil -c icns icon.iconset -o vSNP.icns

# Update app bundle
cp vSNP.icns vSNP.app/Contents/Resources/

# Clean up
rm -rf icon.iconset

# Clear icon cache to show new icon immediately
touch vSNP.app
killall Finder 2>/dev/null || true

echo ""
echo "✓ Icon updated to: $NAME"
echo "✓ App bundle updated"
echo ""
echo "The new icon should appear shortly!"
echo "If you don't see it, try logging out and back in."
