# Casa Radar — imagem para runner em casa (Raspberry Pi / PC / VM).
# A base oficial do Playwright já traz o Chromium e as dependências de sistema,
# por isso o idealista funciona out-of-the-box (e num IP residencial, sem
# bloqueio de datacenter).
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Uma corrida e sai; o agendamento fica a cargo do cron/compose (ver README).
CMD ["python", "main.py", "--once"]
