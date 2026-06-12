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

### Dari GitHub

Jalankan di Ubuntu 24:

```bash
curl -fsSL https://raw.githubusercontent.com/kacalayar/spider/main/install.sh -o /tmp/spider-bridge-install.sh
sudo bash /tmp/spider-bridge-install.sh
```

Installer akan mengambil file pendukung dari repo GitHub ini jika folder `files/`
tidak tersedia secara lokal:

```text
https://raw.githubusercontent.com/kacalayar/spider/main
```

Jika branch atau fork berbeda, override raw URL:

```bash
sudo bash /tmp/spider-bridge-install.sh \
  --repo-raw-url https://raw.githubusercontent.com/kacalayar/spider/main
```

Mode non-interaktif dari GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/kacalayar/spider/main/install.sh -o /tmp/spider-bridge-install.sh
sudo bash /tmp/spider-bridge-install.sh \
  --spider-api-key "SPIDER_API_KEY" \
  --telegram-bot-token "TELEGRAM_BOT_TOKEN" \
  --telegram-admin-ids "123456789" \
  --proxy-user "myuser" \
  --proxy-pass "mypassword" \
  --port 3128 \
  --swap-size-gb 2 \
  --country ID \
  --pool residential
```

### Dari Clone Repo

Jika repo sudah di-clone:

```bash
sudo bash install.sh
```

Mode non-interaktif dari clone repo:

```bash
sudo bash install.sh \
  --spider-api-key "SPIDER_API_KEY" \
  --telegram-bot-token "TELEGRAM_BOT_TOKEN" \
  --telegram-admin-ids "123456789" \
  --proxy-user "myuser" \
  --proxy-pass "mypassword" \
  --port 3128 \
  --swap-size-gb 2 \
  --country ID \
  --pool residential
```

Jika tidak mengisi `--telegram-admin-ids`, installer mencetak token `/claim`.
Kirim `/claim TOKEN` ke bot Telegram untuk menjadi admin pertama.

## Upgrade Existing Install

Tidak perlu uninstall dulu untuk upgrade. Jalankan installer versi terbaru lagi:

```bash
curl -fsSL https://raw.githubusercontent.com/kacalayar/spider/main/install.sh -o /tmp/spider-bridge-install.sh
sudo bash /tmp/spider-bridge-install.sh
```

Jika `/etc/spider-bridge/config.env` sudah ada, installer memakai nilai lama
sebagai default prompt. Argumen CLI tetap menang jika Anda ingin override.

Contoh upgrade sekaligus pindah upstream Spider ke HTTPS `8889`:

```bash
curl -fsSL https://raw.githubusercontent.com/kacalayar/spider/main/install.sh -o /tmp/spider-bridge-install.sh
sudo bash /tmp/spider-bridge-install.sh \
  --spider-upstream-scheme https \
  --spider-upstream-port 8889
```

Uninstall hanya diperlukan jika ingin membersihkan service/config lama sepenuhnya.

## Swap

Installer menawarkan pembuatan swap file secara interaktif. Default-nya `2 GB`,
cocok untuk VPS RAM 2 GB. Isi `0` jika ingin skip.

Jika VPS sudah punya swap aktif, installer tidak membuat swap baru.

Non-interaktif:

```bash
sudo bash install.sh --swap-size-gb 2 ...
```

Disable swap creation:

```bash
sudo bash install.sh --no-swap ...
```

Path default swap file:

```text
/swapfile
```

Jika ingin path lain:

```bash
sudo bash install.sh --swap-file /swapfile-spider --swap-size-gb 4 ...
```

Swap yang dibuat installer ditandai di `/etc/fstab` dengan
`spider-bridge-swap`, sehingga uninstaller bisa mengenalinya.

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

## Spider Upstream Port

Default installer memakai upstream:

```text
http://proxy.spider.cloud:8888
```

Alasannya: VPS menjalankan Squid sebagai HTTP forward proxy. Untuk akses HTTPS,
client tetap memakai method `CONNECT` ke proxy VPS, lalu Squid meneruskan tunnel
itu ke parent proxy Spider. Payload HTTPS client tetap terenkripsi end-to-end ke
website tujuan; port `8888` hanya berarti koneksi Squid ke parent proxy Spider
memakai protokol HTTP proxy.

Spider juga menyediakan endpoint HTTPS proxy:

```text
https://proxy.spider.cloud:8889
```

Jika ingin koneksi dari VPS ke Spider juga memakai TLS, install dengan:

```bash
sudo bash install.sh --spider-upstream-scheme https
```

Atau eksplisit:

```bash
sudo bash install.sh \
  --spider-upstream-scheme https \
  --spider-upstream-port 8889
```

Installer akan menambahkan opsi `tls` pada `cache_peer` Squid untuk mode
`https`.

Upstream juga bisa diganti dari Telegram setelah bot versi terbaru terpasang:

```text
/setupstream https
/setupstream http
/setupstream https 8889
```

## Live Proxy Check

Perintah `/status` dan `/test` melakukan request live lewat proxy lokal ke
`https://api.ipify.org?format=json`. Output akan menampilkan:

```text
Proxy live check: OK/FAIL
Exit IP via local proxy: ...
Direct VPS IP: ...
Exit comparison: DIFFERENT_FROM_VPS/SAME_AS_VPS
```

Jika `Proxy live check` gagal, chain `client -> VPS Squid -> Spider` belum
berhasil. Jika `Exit IP via local proxy` sama dengan `Direct VPS IP`, traffic
belum keluar dengan IP berbeda dari VPS atau upstream Spider sedang memberi
exit yang sama menurut target pengecekan.

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
/setupstream https
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
sudo spider-bridge-uninstall --dry-run
```

Uninstall normal:

```bash
sudo spider-bridge-uninstall
```

Tanpa prompt:

```bash
sudo spider-bridge-uninstall --yes
```

Default uninstaller menghapus service bot, file bridge, config `/etc/spider-bridge`,
cache `/var/lib/spider-bridge`, user file Squid, dan merestore backup Squid
`/etc/squid/squid.conf.pre-spider-bridge.*` terbaru jika ada. Paket OS tidak
dihapus kecuali memakai:

```bash
sudo spider-bridge-uninstall --yes --purge-packages
```

Jika command `spider-bridge-uninstall` belum ada, ambil dari GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/kacalayar/spider/main/uninstall.sh -o /tmp/spider-bridge-uninstall.sh
sudo bash /tmp/spider-bridge-uninstall.sh
```

Opsi berguna:

```text
--keep-config
--keep-state
--keep-swap
--no-restore-squid
--no-stop-squid
```
