FROM openvino:2026.2.1

# OpenVINO GenAI (LLM pipelines + tokenizers) on top of the OpenVINO runtime.
ARG GENAI_URL=https://storage.openvinotoolkit.org/repositories/openvino_genai/packages/2026.2.1/linux/openvino_genai_ubuntu24_2026.2.1.0_x86_64.tar.gz
ADD ${GENAI_URL} /tmp/genai.tgz
RUN mkdir -p /opt/intel/openvino_genai \
 && tar xzf /tmp/genai.tgz -C /opt/intel/openvino_genai --strip-components=1 \
      --exclude='*/samples/*' --exclude='*/docs/*' \
 && rm /tmp/genai.tgz

ENV INTEL_OPENVINO_DIR=/opt/intel/openvino_genai \
    LD_LIBRARY_PATH=/opt/intel/openvino_genai/runtime/lib/intel64:/opt/intel/openvino_genai/runtime/3rdparty/tbb/lib \
    PYTHONPATH=/opt/intel/openvino_genai/python \
    PYTHONUNBUFFERED=1 \
    MODEL_DIR=/models \
    PORT=8080

COPY app /app
WORKDIR /app
EXPOSE 8080
CMD ["python3", "/app/server.py"]
