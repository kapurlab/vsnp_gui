#!/usr/bin/env python3
"""
vSNP Icon Creator - Convert images to macOS .icns icons
"""

import PIL.Image
import PIL.ImageDraw
import numpy as np
import sys
import os

def create_mac_icon(input_path, output_name, remove_bg=True, bg_thresh=50):
    """
    Convert an image to macOS .icns format

    Args:
        input_path: Path to source image (jpg, png, etc.)
        output_name: Output filename (without extension)
        remove_bg: Whether to remove background (default True)
        bg_thresh: Threshold for background removal (default 50)
    """
    print(f"Processing {input_path}...")

    if not os.path.exists(input_path):
        print(f"Error: Could not find {input_path}")
        return None

    try:
        img = PIL.Image.open(input_path).convert("RGBA")
    except Exception as e:
        print(f"Error opening {input_path}: {e}")
        return None

    # 1. Remove Background (Flood fill from corner)
    # Assumes top-left pixel is the background color
    if remove_bg:
        try:
            PIL.ImageDraw.floodfill(img, xy=(0, 0), value=(0, 0, 0, 0), thresh=bg_thresh)
        except Exception as e:
            print(f"Warning: Background removal failed: {e}")

    # 2. Crop to Content
    data = np.array(img)
    alpha = data[:, :, 3]
    non_empty_rows = np.any(alpha > 0, axis=1)
    non_empty_cols = np.any(alpha > 0, axis=0)

    if np.any(non_empty_rows) and np.any(non_empty_cols):
        y_min, y_max = np.where(non_empty_rows)[0][[0, -1]]
        x_min, x_max = np.where(non_empty_cols)[0][[0, -1]]
        img = img.crop((x_min, y_min, x_max + 1, y_max + 1))

    # 3. Resize to standard macOS icon size (1024x1024)
    img = img.resize((1024, 1024), PIL.Image.Resampling.LANCZOS)

    # 4. Save as ICNS and PNG
    icns_path = f"{output_name}.icns"
    png_path = f"{output_name}.png"

    try:
        img.save(icns_path, format='ICNS')
        img.save(png_path, format='PNG')
        print(f"✓ Success! Created {icns_path} and {png_path}")
        return icns_path, png_path
    except Exception as e:
        print(f"Error saving: {e}")
        return None


def main():
    """Main function to process images"""

    # Check if images exist
    images_to_process = []

    # Look for common image files
    for pattern in ['helix', 'sequence', 'dna', 'vsnp']:
        for ext in ['.jpg', '.jpeg', '.png', '.JPG', '.JPEG', '.PNG']:
            for fname in os.listdir('.'):
                if pattern.lower() in fname.lower() and fname.endswith(ext):
                    images_to_process.append(fname)

    # Remove duplicates
    images_to_process = list(set(images_to_process))

    if not images_to_process:
        print("=" * 60)
        print("No images found automatically.")
        print("=" * 60)
        print("\nUsage:")
        print("  1. Place your images in this directory")
        print("  2. Run this script")
        print("\nOr specify images directly:")
        print(f"  python3 {sys.argv[0]} image1.jpg image2.png ...")
        print("\nExample:")
        print(f"  python3 {sys.argv[0]} helix.jpg sequence.png")
        print("=" * 60)

        # Check if user provided files as arguments
        if len(sys.argv) > 1:
            images_to_process = sys.argv[1:]
        else:
            return

    print("=" * 60)
    print("vSNP Icon Creator")
    print("=" * 60)
    print(f"\nFound {len(images_to_process)} image(s) to process:")
    for img in images_to_process:
        print(f"  - {img}")
    print()

    # Process each image
    results = []
    for img_path in images_to_process:
        # Generate output name from filename
        base_name = os.path.splitext(os.path.basename(img_path))[0]
        output_name = f"vSNP_{base_name.title()}"

        result = create_mac_icon(img_path, output_name, remove_bg=True, bg_thresh=50)
        if result:
            results.append(result)
        print()

    # Summary
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    if results:
        print(f"✓ Successfully created {len(results)} icon(s):")
        for icns, png in results:
            print(f"  - {icns}")
            print(f"  - {png}")
        print("\nTo use an icon with your app:")
        print("  cp <icon_name>.icns vSNP.app/Contents/Resources/vSNP.icns")
        print("  touch vSNP.app")
        print("  killall Finder")
    else:
        print("✗ No icons were created")
    print("=" * 60)


if __name__ == "__main__":
    main()
