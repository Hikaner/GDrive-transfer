config:
    rclone config --config ./rclone.config

encrypt:
    openssl enc -aes-256-cbc -pbkdf2 \
        -in rclone.conf \
        -out rclone.conf.enc