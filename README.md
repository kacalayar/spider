# Spider Bridge Proxy VPS

Installer ini menyiapkan VPS Ubuntu 24 sebagai bridge proxy lokal:

- Client konek ke VPS dengan format `VPS_IP:PORT:USER:PASS`.
- VPS menyediakan authenticated HTTP/HTTPS proxy lokal.
- Default engine memakai Squid dan upstream Spider HTTP proxy `8888`.
- Alternatif engine memakai GOST dan upstream Spider SOCKS5 proxy `8887`.
- Bot Telegram mengubah country/pool/engine Spider dan merestart proxy.

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

Jika ingin meniru mode VPS pembanding yang memakai Spider SOCKS5, pilih engine
`gost`:

```bash
curl -fsSL https://raw.githubusercontent.com/kacalayar/spider/main/install.sh -o /tmp/spider-bridge-install.sh
sudo bash /tmp/spider-bridge-install.sh \
  --bridge-engine gost \
  --spider-upstream-scheme socks5 \
  --spider-upstream-port 8887
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
  --bridge-engine gost \
  --spider-upstream-scheme socks5 \
  --spider-upstream-port 8887 \
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
  --bridge-engine gost \
  --spider-upstream-scheme socks5 \
  --spider-upstream-port 8887 \
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

Contoh upgrade sekaligus memastikan upstream Spider memakai mode Squid-compatible `8888`:

```bash
curl -fsSL https://raw.githubusercontent.com/kacalayar/spider/main/install.sh -o /tmp/spider-bridge-install.sh
sudo bash /tmp/spider-bridge-install.sh \
  --spider-upstream-scheme http \
  --spider-upstream-port 8888
```

Uninstall hanya diperlukan jika ingin membersihkan service/config lama sepenuhnya.

## Bridge Engine

`squid` adalah default dan memakai upstream Spider HTTP proxy:

```text
Squid -> http://proxy.spider.cloud:8888
```

`gost` memakai upstream Spider SOCKS5 proxy:

```text
GOST -> socks5://proxy.spider.cloud:8887
```

Mode `gost` lebih dekat dengan VPS pembanding yang memakai GOST/PM2. Local
proxy tetap sama untuk client:

```text
VPS_IP:PORT:LOCAL_USER:LOCAL_PASS
```

Saat mode `gost`, `/status` harus menampilkan:

```text
Spider upstream: socks5://proxy.spider.cloud:8887
```

Jika masih tampil `http://proxy.spider.cloud:8888`, jalankan:

```text
/setupstream socks5
```

Ubah lewat bot:

```text
/setengine gost
/setupstream socks5
```

Kembali ke Squid:

```text
/setengine squid
/setupstream http
```

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

Spider menyediakan endpoint:

```text
http://proxy.spider.cloud:8888
https://proxy.spider.cloud:8889
```

Endpoint `8888` dipakai sebagai default untuk Squid karena Squid bertindak
sebagai HTTP forward proxy dan meneruskan request ke parent proxy Spider.
Untuk website HTTPS, client tetap memakai method `CONNECT`; payload HTTPS tetap
tunnel end-to-end ke website tujuan. Berdasarkan diagnosa VPS, endpoint HTTPS
`8889` bisa bekerja untuk request langsung, tetapi tidak kompatibel stabil
dengan `cache_peer` Squid pada konfigurasi ini.

Jika ingin mencoba upstream HTTPS `8889`, install dengan:

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
beberapa target IP-check fallback (`api.ipify`, `icanhazip`, `ident.me`,
`checkip.amazonaws`). Output akan menampilkan:

```text
HTTPS CONNECT check: OK/FAIL
HTTPS CONNECT check exit IP: ...
HTTP check: OK/FAIL
HTTP check exit IP: ...
Direct VPS IP: ...
Exit comparison: DIFFERENT_FROM_VPS/SAME_AS_VPS
```

Jika check gagal, chain `client -> VPS Squid -> Spider` belum berhasil untuk
jenis request tersebut. Jika exit IP sama dengan `Direct VPS IP`, traffic belum
keluar dengan IP berbeda dari VPS atau upstream Spider sedang memberi exit yang
sama menurut target pengecekan.

Jika muncul:

```text
Tunnel connection failed: 403 Forbidden
```

dan upstream masih `http://proxy.spider.cloud:8888`, berarti target CONNECT
tersebut ditolak pada jalur HTTP parent. Coba target website real. Jika target
yang sama bisa lewat VPS pembanding GOST/SOCKS5, gunakan:

```text
/setengine gost
```

```text
/status
```

Jika muncul `502 Bad Gateway`, jalankan:

```text
/diag
```

`/diag` akan membandingkan test lewat proxy lokal dengan test langsung ke
upstream Spider. Jika direct Spider berhasil tapi jalur lokal gagal, masalahnya
ada di engine bridge atau service lokal. Jika direct Spider juga gagal, cek API
key, saldo/quota Spider, pool/country, atau koneksi outbound VPS ke Spider.
Jika error berisi `Blocked by network blocker`, target test IP-check sedang
diblok oleh policy Spider; coba target website real atau ganti pool/country.

`/status` juga mengecek fraud/risk score untuk exit IP yang terdeteksi. Prioritas
IP yang dicek adalah exit IP dari HTTPS, lalu HTTP jika HTTPS tidak tersedia.
Lookup memakai `proxycheck.io`; jika API eksternal itu rate-limit atau timeout,
status proxy tetap tampil dan fraud score ditandai unavailable.

Pesan `/status` dan `/showproxy` menyertakan tombol copy proxy. Tombol ini
memakai fitur `copy_text` Telegram; pada client Telegram lama, proxy tetap bisa
di-copy manual dari teks `<code>...</code>`.

Untuk test website real dari Telegram:

```text
/testurl https://example.com
/testurl https://httpbin.org/ip
```

Jika direct Spider OK tetapi Squid path gagal saat upstream `https://...:8889`,
gunakan:

```text
/setupstream http
```

Jika Squid HTTP parent tetap menolak CONNECT ke target HTTPS tertentu, coba
mode GOST/SOCKS5:

```text
/setengine gost
/setupstream socks5
```

Jika HTTP check menampilkan exit IP `127.0.0.1`, upgrade dan apply ulang config.
Versi terbaru menulis `forwarded_for delete` di Squid agar header client lokal
tidak ikut diteruskan ke Spider/target. Header `Via` tetap aktif karena proxy
HTTP memang wajib menggunakannya.

Jika saat install muncul warning:

```text
WARNING: HTTP requires the use of Via
```

berarti VPS masih memakai config/script lama yang berisi `via off`. Cek:

```bash
grep -n "via off" /etc/squid/squid.conf /usr/local/sbin/spider-bridge-apply
```

Jika masih ada output, jalankan ulang installer versi terbaru dari GitHub lalu
apply ulang config:

```bash
curl -fsSL https://raw.githubusercontent.com/kacalayar/spider/main/install.sh -o /tmp/spider-bridge-install.sh
sudo bash /tmp/spider-bridge-install.sh \
  --spider-upstream-scheme http \
  --spider-upstream-port 8888
```

Jika bot berulang mengirim pesan `Menerapkan upstream...` tanpa command baru,
upgrade ke versi terbaru. Versi lama membuat service bot bergantung langsung
pada `squid.service`, sehingga bot bisa ikut restart saat Squid di-restart oleh
`/setupstream` dan Telegram mengirim ulang update lama.

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
/setengine gost
/setengine squid
/setupstream socks5
/setupstream https
/showproxy
/test
/testurl https://example.com
/diag
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
systemctl status spider-bridge-proxy --no-pager
systemctl status spider-bridge-bot --no-pager
journalctl -u spider-bridge-proxy -f
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

Default uninstaller menghapus service bot, service GOST bridge jika ada, file
bridge, config `/etc/spider-bridge`, cache `/var/lib/spider-bridge`, user file
Squid, dan merestore backup Squid `/etc/squid/squid.conf.pre-spider-bridge.*`
terbaru jika ada. Binary `/usr/local/bin/gost` hanya dihapus jika installer ini
yang memasangnya. Paket OS tidak dihapus kecuali memakai:

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
