FROM python:3.10

WORKDIR /kindness-box/

RUN pip install pdm==2.1.4
RUN pdm config check_update false && pdm config python.use_venv false

COPY pyproject.toml pdm.lock* gunicorn.conf.py .env  ./
COPY app app/

ENTRYPOINT ["pdm", "run", "dotenv", "-f", ".env", "run", "python", "-m", "app"]