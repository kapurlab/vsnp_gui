# Linux Testing Checklist

1) Clone repo
```
git clone https://github.com/vkapur/vsnp_gui.git
cd vsnp_gui
```

2) Install dependencies
- Python 3.9+
- Node.js + npm
- Conda (recommended)

3) Start GUI
```
./start_gui_linux.sh
```

4) Verify:
- Backend: http://127.0.0.1:8000/api/health
- Frontend: http://localhost:5173
- Browser opens via `xdg-open`

5) File open test
- In Step 2 Results, click **Open** and confirm the file opens

6) Report back
- OS + version
- Errors (if any)
- Whether `xdg-open` worked
