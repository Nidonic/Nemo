FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml ./
COPY nemo_cloud ./nemo_cloud
RUN pip install --no-cache-dir .
ENV NEMO_CLOUD_DB=/data/nemo_cloud.db
VOLUME /data
EXPOSE 8787
CMD ["nemo-cloud"]
