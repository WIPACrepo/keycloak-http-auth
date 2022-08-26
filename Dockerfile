FROM python:3.9

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /opt/app && mkdir -p /mnt/data

WORKDIR /opt/app

COPY . .

ENV PYTHONPATH=/opt/app

CMD ["python", "-m", "keycloak_http_auth"]
