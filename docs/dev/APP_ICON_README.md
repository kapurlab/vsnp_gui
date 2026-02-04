# vSNP GUI App Icon & Launcher

## What Was Created

### 1. **Icon Files**
- `icon.png` - High-resolution 1024x1024 icon with DNA helix and vSNP branding
- `vSNP.icns` - macOS icon file containing all required sizes

### 2. **vSNP.app** - macOS Application Bundle
A full macOS application that you can:
- Double-click to launch from Finder
- Drag to your Dock for quick access
- Move to `/Applications` folder
- Launch like any native Mac app

## How to Use

### Quick Start
1. **Double-click `vSNP.app`** to launch the vSNP GUI
2. Your default browser will open to http://localhost:5173
3. The backend and frontend servers will start automatically

### Add to Dock
1. Drag `vSNP.app` to your Dock
2. Click the icon anytime to launch

### Move to Applications
```bash
# Optional: Move to Applications folder for system-wide access
mv vSNP.app /Applications/
```

## Icon Design

The icon features:
- **DNA double helix** representing genomic analysis
- **SNP markers** (yellow dots) highlighting single nucleotide polymorphisms
- **vSNP text** at the bottom for clear branding
- **Teal/green color scheme** matching your GUI theme
- **Professional scientific aesthetic** appropriate for bioinformatics

## Technical Details

### App Bundle Structure
```
vSNP.app/
├── Contents/
│   ├── Info.plist          # App metadata
│   ├── MacOS/
│   │   └── vSNP            # Launcher script
│   └── Resources/
│       └── vSNP.icns       # Icon file
```

### What Happens When You Launch
1. The app executes the launcher script
2. The script finds the `start_gui.sh` in the same directory
3. Backend (FastAPI) starts on port 8000
4. Frontend (React/Vite) starts on port 5173
5. Browser opens automatically to the GUI

## Customization

### To Update the Icon
1. Edit the Python script that created `icon.png`
2. Regenerate the `.icns` file:
   ```bash
   iconutil -c icns icon.iconset -o vSNP.icns
   ```
3. Replace the icon in the app bundle:
   ```bash
   cp vSNP.icns vSNP.app/Contents/Resources/
   ```

## Troubleshooting

### Icon Doesn't Show Up
- Right-click `vSNP.app` → Get Info → check if icon appears
- If not, try rebuilding the icon cache:
  ```bash
  sudo rm -rf /Library/Caches/com.apple.iconservices.store
  sudo find /private/var/folders/ -name com.apple.iconservices -exec rm -rf {} \;
  killall Dock
  ```

### App Won't Launch
- Check that `start_gui.sh` is executable:
  ```bash
  chmod +x start_gui.sh
  ```
- Check that the app launcher is executable:
  ```bash
  chmod +x vSNP.app/Contents/MacOS/vSNP
  ```

### Terminal Window Appears
This is normal - the app runs the backend and frontend servers in Terminal. To hide the Terminal window, you would need to modify the launcher to use a background process (let me know if you want this).

## Files You Can Delete

The following were temporary build files and can be safely deleted:
- `icon.iconset/` (already removed)
- `Launch_vSNP_GUI.command` (optional - replaced by vSNP.app)

## Notes

- The app must stay in the `vsnp_gui` folder (or the folder containing `start_gui.sh`)
- Moving just the `.app` file elsewhere won't work without the supporting files
- If you want a standalone app, let me know and I can create a self-contained version
