# Environment

## Expected variables
Do not place real values in documentation or commits.

```dotenv
SECRET_KEY=
DATABASE_URL=
OPENAI_API_KEY=
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
MAKE_WEBHOOK_SINGLE=
MAKE_WEBHOOK_CAROUSEL=
```

## Local commands
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

## Safety
- Confirm `.env` is in `.gitignore`.
- Never commit webhook URLs, database credentials or API keys.
- Rotate any credential that has been exposed in logs or commits.
