# The API image: standard library only, so there is nothing to install and nothing to
# pin. The graph is not in the image — it is reached over the network, which is what
# keeps a 3 GB database out of a 43 MB container.
FROM python:3.12-slim

WORKDIR /app
COPY expediente/ ./expediente/

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

# Non-root: the API only reads, so it does not need more than that.
RUN useradd --create-home --uid 10001 expediente
USER expediente

CMD ["python3", "-m", "expediente.api.server", "--port", "8080"]
