# Spider Bridge Proxy VPS

Installer ini menyiapkan VPS Ubuntu 24 sebagai bridge proxy lokal:

- Client konek ke VPS dengan format `VPS_IP:PORT:USER:PASS`.
- VPS memakai Squid sebagai authenticated HTTP/HTTPS proxy.
- Squid meneruskan traffic ke upstream Spider Proxy.
- Bot Telegram mengubah country/pool Spider dan merestart Squid.

Referensi Spider:

- Proxy API: `https://spider.cloud/docs/api/proxy`
- Proxy locations: `https://spider.cloud/proxy-locations`

## Install

Jalankan di Ubuntu 24 sebagai root:

```bash
sudo bash install.sh
```

Mode non-interaktif:

```bash
sudo bash install.sh \
  --spider-api-key "SPIDER_API_KEY" \
  --telegram-bot-token "TELEGRAM_BOT_TOKEN" \
  --telegram-admin-ids "123456789" \
  --proxy-user "myuser" \
  --proxy-pass "mypassword" \
  --port 3128 \
  --country ID \
  --pool residential
```

Jika tidak mengisi `--telegram-admin-ids`, installer mencetak token `/claim`.
Kirim `/claim TOKEN` ke bot Telegram untuk menjadi admin pertama.

## Country List

Daftar country tidak di-hardcode. Bot mengambil kode country langsung dari
`https://spider.cloud/proxy-locations`, mengekstrak parameter `country=xx`, lalu
menyimpan cache di:

```text
/var/lib/spider-bridge/countries.json
```

Cache dipakai 24 jam. Gunakan `/refreshcountries` untuk mengambil ulang daftar
country dari Spider.

## Country Parameter

Dokumentasi API Proxy Spider menyebut parameter `country_code`, sedangkan halaman
Proxy Locations menampilkan contoh `country=us`. Installer default ke
`country_code`, tetapi bisa diganti:

```bash
sudo bash install.sh --country-param country ...
```

Atau dari Telegram:

```text
/setcountryparam country
/setcountryparam country_code
```

## Command Bot

```text
/status
/countries
/refreshcountries
/pools
/setcountry ID
/setcountry off
/setproxy residential
/setcountryparam country_code
/showproxy
/test
/apply
/setlocaluser USER
/setlocalpass PASSWORD
/setport 3128
/whoami
/addadmin USER_ID
/deladmin USER_ID
```

## Service

```bash
systemctl status squid --no-pager
systemctl status spider-bridge-bot --no-pager
journalctl -u spider-bridge-bot -f
```

Apply ulang config:

```bash
/usr/local/sbin/spider-bridge-apply
```

## Uninstall

Dry-run dulu:

```bash
sudo bash uninstall.sh --dry-run
```

Uninstall normal:

```bash
sudo bash uninstall.sh
```

Tanpa prompt:

```bash
sudo bash uninstall.sh --yes
```

Default uninstaller menghapus service bot, file bridge, config `/etc/spider-bridge`,
cache `/var/lib/spider-bridge`, user file Squid, dan merestore backup Squid
`/etc/squid/squid.conf.pre-spider-bridge.*` terbaru jika ada. Paket OS tidak
dihapus kecuali memakai:

```bash
sudo bash uninstall.sh --yes --purge-packages
```

Opsi berguna:

```text
--keep-config
--keep-state
--no-restore-squid
--no-stop-squid
```
