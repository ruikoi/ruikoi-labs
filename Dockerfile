FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ninja-build pkg-config git ca-certificates curl wget \
    python3 python3-venv python3-pip libglib2.0-dev libpixman-1-dev \
    libslirp-dev liblzma-dev libfdt-dev zlib1g-dev \
    bc bison flex libssl-dev libelf-dev cpio rsync file unzip xz-utils lz4 mtools \
    clang lld gcc-aarch64-linux-gnu gcc-riscv64-linux-gnu \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
CMD ["bash"]
