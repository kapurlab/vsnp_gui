#!/bin/bash

# Quick icon switcher for vSNP app

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "vSNP Icon Switcher"
echo "=================="
echo ""
echo "Available icons:"
echo "  1) Glossy 3D Helix     (vSNP_Glossy_Helix.icns)"
echo "  2) Sequence Alignment  (vSNP_Sequence_Alignment.icns)"
echo ""
echo -n "Choose an icon (1-2): "
read choice

case $choice in
    1)
        SOURCE="vSNP_Glossy_Helix.icns"
        NAME="Glossy 3D Helix"
        ;;
    2)
        SOURCE="vSNP_Sequence_Alignment.icns"
        NAME="Sequence Alignment"
        ;;
    *)
        echo "Invalid choice!"
        exit 1
        ;;
esac

if [ ! -f "$SOURCE" ]; then
    echo "Error: $SOURCE not found!"
    exit 1
fi

echo ""
echo "Switching to: $NAME"

# Copy icon to app bundle
cp "$SOURCE" vSNP.app/Contents/Resources/vSNP.icns

# Refresh Finder to show new icon
touch vSNP.app
killall Finder 2>/dev/null || true

echo ""
echo "✓ Icon updated successfully!"
echo "✓ The new icon should appear in a few seconds"
echo ""
