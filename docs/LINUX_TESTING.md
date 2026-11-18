# Linux Testing Checklist

1) Clone repo
```
git clone https://github.com/kapurlab/vsnp_gui.git
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
- Frontend: http://localhost:5173 (open it in your browser; the script does not open one for you)

5) File view and download test
- In Step 2 Results, click **View** on the summary `.html` and confirm it opens in a new browser tab
- Click **DL** on a file and confirm it downloads

6) Report back
- OS + version
- Errors (if any)
- Whether **View** and **DL** worked
