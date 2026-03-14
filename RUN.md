**1. Check Python is installed:**
```
python --version
```
If not installed, grab it from [python.org](https://www.python.org/downloads/) — check "Add to PATH" during install.

**2. Create a virtual environment (keeps things clean):**
```
cd (to the dir main.py is in)
python -m venv venv
venv\Scripts\activate
```

**3. Install dependencies:**
```
pip install -r requirements.txt
```

**4. Run it:**
```
python main.py
```

It'll list your audio devices, ask you to pick an input (microphone) and output (speakers/headphones), then start listening.