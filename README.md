# Layaq Trading Journal Super App

An offline, English-language trading journal for Windows with before/after HTF, MTF, and LTF screenshots.

## Build EXE in GitHub without installing Python locally

1. Create a GitHub account and a new repository (Private recommended).
2. Upload `app.py`, `README.md`, and `.github/workflows/build-windows.yml` keeping the directory path unchanged. When using GitHub's browser upload, you can drag the entire `.github` folder and the two root files into the upload area.
3. Commit to the `main` branch; this launches a build automatically. Alternatively open **Actions** > **Build Windows EXE** > **Run workflow**.
4. When the build finishes, open the run, find **Artifacts**, and download **LayaqTradingJournal-Windows**. Unzip it to access `LayaqTradingJournal.exe`.
5. Copy EXE anywhere on a Windows 10/11 PC. Windows SmartScreen might display a warning for unsigned software. Only run it if you trust the source.

The EXE operates offline. Data and screenshots are stored in your Windows user profile (Local AppData) and need periodic backups.

Note: GitHub Actions usage and storage limits depend on your GitHub plan. Build output size and transfer usage vary. The EXE is not prebuilt in this download.
