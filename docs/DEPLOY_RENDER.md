# Deploy BALANCE ke Render — satu alamat untuk dasbor dan AI

## Pilihan dan biaya (diperiksa 8 November 2026)

Gunakan satu Web Service Docker pada paket compute 0.5c-512mb (nama lama Starter): US$7/bulan. Tambahkan disk persisten 1 GB: US$0,25/bulan. Perkiraan dasar US$7,25/bulan sebelum pajak, kurs pembayaran, dan pemakaian tambahan. Pilih workspace dasar sesuai kebutuhan; tidak perlu paket kolaborasi berbayar untuk demo satu layanan ini. Periksa total pada dashboard Render sebelum mengaktifkan resource berbayar.

Frontend dibangun menjadi berkas statis lalu dilayani Flask, sehingga tidak memerlukan layanan frontend berbayar kedua. SQLite disimpan pada disk persisten; tidak perlu membuat Postgres untuk arsitektur ini. Disk persisten tersedia untuk layanan berbayar. Gunakan satu instance.

Alternatif Railway Hobby memiliki minimum US$5/bulan termasuk kredit pemakaian US$5; biaya aktual mengikuti CPU, RAM, volume dan jaringan. Untuk mengejar tenggat, Render dipilih karena konfigurasi proyek sudah tersedia dan biaya dasar satu layanan lebih mudah diperkirakan. Ini pilihan implementasi, bukan klaim Render selalu paling murah.

## 1. Siapkan repository tim

Buat repository GitHub privat khusus paket BALANCE_JUDGE_DEMO_V3 menggunakan GitHub Desktop. Letakkan isi paket pada root repository: render.yaml dan Dockerfile harus langsung terlihat, bukan di dalam lapisan folder tambahan.

Sebelum memublikasikan repository privat, periksa daftar berkas. .gitignore yang disertakan mengecualikan .venv, node_modules, .state, config.local.json, .env, dan config.h. Dataset Excel mitra, catatan akses, serta database lokal tidak dibutuhkan di repository hosting ini. Source model dan generator sintetis sudah termasuk.

Repository SAMUDERA/Aquara tidak perlu diubah. File build frontend/dist boleh dipakai lokal; Docker membangunnya kembali dari source saat hosting.

## 2. Hubungkan Render

Buka https://dashboard.render.com, masuk/daftar menggunakan akun tim, lalu hubungkan repository GitHub BALANCE yang baru. Pada halaman pembuatan Blueprint, pilih repository dan branch yang berisi render.yaml.

Periksa konfigurasi berikut pada ringkasan sebelum membuat layanan:

| Pengaturan | Nilai |
|---|---|
| Nama layanan | balance-judge-demo (boleh diberi nama unik) |
| Runtime | Docker |
| Compute | 0.5c-512mb / 0,5 CPU / 512 MB |
| Region | Singapore |
| Disk | 1 GB, mount /var/data |
| BALANCE_STATE_DIR | /var/data/balance |
| Health check | /api/balance/v1/health |
| BALANCE_COOKIE_SECURE | true |
| ENABLE_ACTUATION | false |
| JUDGE_USERNAME | juri |
| JUDGE_PASSWORD | BALANCE-Demo2026! (diisi manual) |

DASHBOARD_KEY dan DEVICE_KEY dibuat otomatis oleh Render. Keduanya merupakan akses tim, bukan akun juri; jangan menampilkan nilainya di dokumentasi atau frontend. Isi JUDGE_PASSWORD secara manual pada environment Render sesuai tabel; render.yaml tetap menggunakan sync: false. Akun demo juri sengaja ditampilkan kepada pengunjung pada halaman login untuk SIMULASI. Password pribadi, akun hosting, GitHub, dan akses operator tetap privat milik tim.

## 3. Tunggu build dan deploy

Docker membangun React dan menyiapkan model sintetis dengan versi library hosting yang dipatok. Saat runtime pertama, template model disalin ke disk persisten. Waitress melayani frontend dan API pada satu port dari environment PORT.

Jika build gagal, salin pesan error dari log (tanpa nilai environment rahasia). Perhatikan tahap yang gagal: npm, pip, persiapan model, atau startup. Jangan menambahkan proses Flask development server atau layanan frontend kedua untuk menutup masalah startup.

## 4. Uji URL Live

Setelah Render menampilkan Live, gunakan URL asli yang diberikan Render, bukan IP laptop dan bukan alamat contoh pada dokumen ini.

- Buka /api/balance/v1/health pada URL layanan: status harus READY.
- Buka alamat dasar layanan: halaman akun juri tampil langsung. /?demo=1 juga tetap dapat digunakan.
- Masuk memakai username juri dan JUDGE_PASSWORD hosting, hitung rekomendasi, ubah pasokan, jalankan replay, coba telemetri SIMULASI, lihat Riwayat, dan Keluar.
- Pastikan akun juri tidak menampilkan kendali pompa/pintu.
- Lakukan restart layanan dari dashboard Render, kemudian pastikan riwayat masih ada. Ini membuktikan penempatan database pada disk; jangan menghapus database untuk melewati pemeriksaan.

Pilihan tes HTTP dari laptop, setelah URL benar-benar Live:

```powershell
& "C:\Users\Fajar\Downloads\BALANCE_INTEGRATED_V1\.venv\Scripts\python.exe" .\tools\check_judge_demo.py "https://ALAMAT-ASLI-DARI-RENDER.onrender.com"
```

Script meminta username dan password secara interaktif; password tidak ditampilkan. Hasil berhasil: JUDGE_HTTP_PASS. Alamat pada contoh wajib diganti. Script membuat rencana dan telemetri SIMULASI untuk pengujian, tidak mengirim perintah perangkat.

## 5. Siapkan akses juri

Isi template docs/AKSES_JURI.md dengan URL Live setelah Render aktif; sebelum itu pertahankan placeholder URL. Akun demo tersedia langsung pada halaman login, sehingga juri tidak perlu mencari password di berkas pengumpulan. Uji dari HP dengan jaringan berbeda dan mode browser privat agar akses tidak bergantung pada sesi operator atau Wi-Fi laptop.

## Batas verifikasi

Build Docker penuh dan URL publik baru dapat diverifikasi pada layanan Render; Docker tidak tersedia di lingkungan penyusun. Pengujian lokal tercatat di evidence. Hasil profil memori satu proses merupakan titik awal pemilihan 512 MB, bukan uji beban beberapa juri sekaligus. Jika terjadi OOM, periksa metrik dan sesuaikan compute; jangan mengganti hasil model atau mematikan autentikasi.

Penyambungan ESP32 ke hosting ditunda sesuai pembagian kerja tim. ENABLE_ACTUATION tetap false untuk demo publik Tahap 2.

## Sumber resmi

- https://render.com/pricing
- https://render.com/docs/compute-plans
- https://render.com/docs/blueprint-spec
- https://render.com/docs/disks
- https://docs.railway.com/pricing/plans
