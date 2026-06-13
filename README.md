# Spider Bridge Proxy VPS

Installer ini menyiapkan VPS Ubuntu 24 sebagai bridge proxy lokal:

- Client konek ke VPS dengan format `VPS_IP:PORT:USER:PASS`.
- VPS menyediakan authenticated HTTP/HTTPS proxy lokal lewat GOST.
- Upstream Spider memakai SOCKS5 `socks5://proxy.spider.cloud:8887`.
- Bot Telegram mengubah country/pool Spider, test proxy, dan restart service.

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

Installer mengambil file pendukung dari repo GitHub ini jika folder `files/`
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

`--bridge-engine gost`, `--spider-upstream-scheme socks5`, dan
`--spider-upstream-port 8887` tidak perlu ditulis lagi karena sudah menjadi
default dan satu-satunya mode yang didukung.

Gunakan `--pool default` jika ingin meniru VPS pembanding: installer tidak
mengirim parameter `proxy=...` ke Spider, tetapi negara tetap bisa diatur
dengan `--country ID` atau command `/setcountry`.

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

Jalankan installer versi terbaru lagi:

```bash
curl -fsSL https://raw.githubusercontent.com/kacalayar/spider/main/install.sh -o /tmp/spider-bridge-install.sh
sudo bash /tmp/spider-bridge-install.sh
```

Jika `/etc/spider-bridge/config.env` lama masih berisi `BRIDGE_ENGINE=squid`,
installer akan mengabaikannya dan memakai `gost`. Jika upstream lama masih
`8888` atau `8889`, apply script akan mengarahkannya ke `socks5:8887`.

Uninstall hanya diperlukan jika ingin membersihkan service/config lama
sepenuhnya.

## Engine

Mode saat ini GOST-only:

```text
Client -> VPS GOST HTTP proxy -> Spider SOCKS5 8887
```

Local proxy tetap sama untuk client:

```text
VPS_IP:PORT:LOCAL_USER:LOCAL_PASS
```

`/status` harus menampilkan engine GOST tanpa membuka detail upstream Spider:

```text
Engine: gost
```

Command migrasi ini tetap aman dijalankan:

```text
/setengine gost
/setupstream socks5
```

`/setengine squid`, `/setupstream http`, dan `/setupstream https` tidak lagi
didukung.

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

## Pool Spider

Pool bisa dipilih dari installer atau bot:

```bash
sudo bash install.sh --pool residential
```

```text
/pools
/setproxy residential
/setproxy mobile
/setproxy isp
```

Mode `default` tidak mengirim `proxy=...` ke Spider. Ini meniru VPS pembanding
yang password upstream-nya hanya berisi lokasi, misalnya:

```text
country_code=SG
```

Command-nya:

```text
/setproxy default
/setcountry SG
```

Jika ingin menentukan pool lagi, jalankan `/setproxy residential`,
`/setproxy mobile`, atau pool lain dari `/pools`.

## User Rental

Admin bot bisa membuat proxy terpisah untuk user Telegram. Setiap user mendapat
service GOST sendiri, port sendiri, username/password sendiri, dan setting
country/pool sendiri. Dengan model ini, saat user A mengubah country, user B
tidak ikut berubah.

Tambah user dengan expired 30 hari:

```text
/adduser 123456789 30d country=SG pool=default
```

Format expired yang didukung:

```text
12h
30d
2026-07-13
2026-07-13 23:59
never
```

Credential dan port akan dibuat otomatis dari range `32000-32999`. Jika perlu,
admin bisa menentukan manual:

```text
/adduser 123456789 30d user=client01 pass=secret01 port=32010 country=ID pool=residential
```

Hapus user:

```text
/deluser 123456789
```

Lihat user rental:

```text
/listuser
```

Output list menampilkan port, username, country, pool, tanggal expired, sisa
masa aktif, dan status active/expired.

User rental bisa memakai command:

```text
/status
/showproxy
/countries
/pools
/setcountry SG
/setcountry off
/setproxy residential
/test
/testurl https://example.com
```

Menu command Telegram memakai scope:

- user belum terdaftar hanya melihat command publik minimal seperti `/start`,
  `/help`, `/whoami`, dan `/claim`;
- user rental aktif melihat command user;
- admin melihat command admin penuh.

Walaupun command admin diketik manual oleh user biasa, bot tetap menolak karena
akses dicek dari Telegram user ID di sisi bot.

Saat expired, bot akan mematikan service user tersebut dalam loop cleanup
berkala. Jika bot sedang mati saat waktu expired lewat, service akan dihentikan
saat bot hidup kembali.

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
Fraud score: ...
Spider balance: ...
```

Jika IP keluar berbeda dari `Direct VPS IP`, traffic sudah melewati Spider.
Jika target IP-check diblok oleh policy Spider, coba target website real:

```text
/testurl https://whoer.net
/testurl https://example.com
```

`/diag` khusus admin. Command ini membandingkan test lewat proxy lokal dengan
test langsung ke upstream Spider. Jika direct Spider berhasil tapi jalur lokal
gagal, cek service `spider-bridge-proxy`. Jika direct Spider juga gagal, cek API
key, saldo/quota Spider, pool/country, atau koneksi outbound VPS ke Spider.

Pesan `/status` dan `/showproxy` menyertakan tombol copy proxy. Tombol ini
memakai fitur `copy_text` Telegram; pada client Telegram lama, proxy tetap bisa
di-copy manual dari teks `<code>...</code>`.

## Command Bot

Command admin:

```text
/status
/countries
/refreshcountries
/pools
/setcountry ID
/setcountry off
/setproxy residential
/setproxy default
/setcountryparam country_code
/setengine gost
/setupstream socks5
/showproxy
/test
/testurl https://example.com
/diag
/balance
/apply
/setlocaluser USER
/setlocalpass PASSWORD
/setport 3128
/whoami
/addadmin USER_ID
/deladmin USER_ID
/adduser USER_ID 30d
/deluser USER_ID
/listuser
```

Command user rental hanya command yang tercantum di bagian User Rental.

## Service

```bash
systemctl status spider-bridge-proxy --no-pager
systemctl status spider-bridge-user-123456789 --no-pager
systemctl status spider-bridge-bot --no-pager
journalctl -u spider-bridge-proxy -f
journalctl -u spider-bridge-user-123456789 -f
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

Default uninstaller menghapus service bot, service GOST bridge utama, semua
service rental `spider-bridge-user-*.service`, file bridge, config
`/etc/spider-bridge`, cache `/var/lib/spider-bridge`, dan swap yang dibuat
installer. Binary `/usr/local/bin/gost` hanya dihapus jika installer ini yang
memasangnya.

Untuk membersihkan sisa install lama berbasis Squid, jalankan:

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
--purge-packages
```
