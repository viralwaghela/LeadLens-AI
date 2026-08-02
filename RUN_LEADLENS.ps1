Set-Location $PSScriptRoot
python -m pip install -r requirements.txt
python -m streamlit run app.py --server.port 8504
