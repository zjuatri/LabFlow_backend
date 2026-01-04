FROM python:3.11-slim

ARG TYPST_VERSION=0.14.2

WORKDIR /app

RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
	ca-certificates \
	curl \
	xz-utils \
	fontconfig \
	fonts-noto-cjk \
	gcc \
	pkg-config \
	default-libmysqlclient-dev \
	libreoffice-common \
	libreoffice-writer \
	libreoffice-impress \
	&& rm -rf /var/lib/apt/lists/*

# Install Typst CLI
RUN curl -fsSL -o /tmp/typst.tar.xz \
	"https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/typst-x86_64-unknown-linux-musl.tar.xz" \
	&& mkdir -p /tmp/typst \
	&& tar -xJf /tmp/typst.tar.xz -C /tmp/typst --strip-components=1 \
	&& install -m 0755 /tmp/typst/typst /usr/local/bin/typst \
	&& rm -rf /tmp/typst /tmp/typst.tar.xz

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV TYPST_BIN=/usr/local/bin/typst

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
