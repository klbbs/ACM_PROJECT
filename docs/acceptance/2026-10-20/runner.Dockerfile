FROM acm-lab:0.10.1-judge0 AS keys
FROM judge0:1.13.1-cgroup2
USER root
COPY --from=keys /usr/share/keyrings/debian-archive-keyring.gpg /usr/share/keyrings/debian-archive-keyring.gpg
RUN rm -f /etc/apt/sources.list.d/* && printf "deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://mirrors.cloud.tencent.com/debian bookworm main\n" > /etc/apt/sources.list && apt-get update && apt-get download libcrypt1 && mkdir -p /opt/bootstrap && dpkg-deb -x libcrypt1_*.deb /opt/bootstrap && export LD_LIBRARY_PATH=/opt/bootstrap/lib/x86_64-linux-gnu && rm libcrypt1_*.deb && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends g++-12 && rm -rf /var/lib/apt/lists/*
USER judge0
