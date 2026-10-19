FROM judge0:1.13.1-cpp20
USER root
COPY patch.py /tmp/patch.py
RUN /usr/bin/python3 /tmp/patch.py && rm /tmp/patch.py
USER judge0
