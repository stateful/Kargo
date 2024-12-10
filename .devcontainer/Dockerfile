FROM ghcr.io/containercraft/devcontainer:latest AS bootstrap
RUN curl -fsSL "https://github.com/jlewi/foyle/releases/download/v0.0.41/foyle_0.0.41_linux_amd64" -o "/tmp/foyle"

FROM ghcr.io/containercraft/devcontainer:latest

# Additional packages
RUN sudo apt-get update \
    && sudo apt-get install -y \
    btop \
    file \
    gh \
    gnupg \
    gnupg2 \
    jq \
    pen \
    ruby-full \
    php-cli \
    lua5.4 \
    perl \
    socat \
    tar \
    tmux \
    tree \
    && sudo rm -rf /var/lib/apt/lists/*


# Gcloud SDK
RUN echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    | sudo tee -a /etc/apt/sources.list.d/google-cloud-sdk.list \
    && curl https://packages.cloud.google.com/apt/doc/apt-key.gpg \
    | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && sudo apt-get update -y \
    && sudo apt-get install google-cloud-cli -y

ADD --chown=vscode:vscode config.yaml /home/vscode/.foyle/config.yaml
COPY --from=bootstrap --chown=vscode:vscode /tmp/foyle /usr/local/bin/foyle
RUN chmod +x /usr/local/bin/foyle
