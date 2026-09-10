FROM python:3.12-slim
WORKDIR /app
COPY app/ /app/
EXPOSE 7443 7500/udp 7501/udp 7502/udp 8080
CMD ["python", "-u", "main.py"]
