FROM python:3.8.3-slim-buster

WORKDIR /cs2bot

COPY . .

RUN pip install --no-cache-dir -r ./requirements.txt

ENTRYPOINT ["python", "./main.py"]