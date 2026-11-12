# Starts the app using the project-local Python (no system install needed).
Set-Location $PSScriptRoot
.\.venv\Scripts\python.exe -m streamlit run app.py
