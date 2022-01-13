gFROM python:3.9

RUN useradd -m -U app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

WORKDIR /home/app
USER app

COPY . .

ENV PYTHONPATH=/home/app

CMD ["python", "-m", "keycloak_http_auth"]
