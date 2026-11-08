# BALANCE — Demo juri Tahap 2 (V3)

Fokus paket: dasbor web + model ML + optimasi LP + jadwal/replay simulasi. Prototipe IoT dikerjakan dan didemonstrasikan terpisah oleh penanggung jawab perangkat. Demo publik tidak mengendalikan alat fisik.

## Yang sudah disiapkan

- UI mengikuti warna, tema, sidebar, susunan panel, urutan menu, dan grid versi teman tim. Perubahan meliputi tipografi, ikon, logo, bahasa antarmuka, dan akses masuk yang mendahulukan akun juri.
- Backend prediksi/LP/scheduler dan kontrak API V2 dipertahankan. Data pelatihan tetap sintetis.
- React hasil build sudah ada di frontend/dist; untuk mencoba lokal cukup menjalankan Flask. Tidak perlu npm install/build ulang jika tidak mengubah frontend.
- render.yaml dan Dockerfile menyiapkan satu layanan HTTPS beserta penyimpanan persisten.
- Logo SVG dan PNG tersedia di folder brand; sumber untuk aplikasi ada di frontend/public/brand.

## Jalankan di laptop Fajar dengan environment yang sudah ada

Ekstrak sebagai folder baru C:\Users\Fajar\Downloads\BALANCE_JUDGE_DEMO_V3. Pertahankan folder V2 sebagai cadangan. Hentikan backend V2 dengan Ctrl+C pada terminalnya jika port 5000 sedang dipakai.

Buka PowerShell, jalankan:

```powershell
cd "C:\Users\Fajar\Downloads\BALANCE_JUDGE_DEMO_V3"
& "C:\Users\Fajar\Downloads\BALANCE_INTEGRATED_V1\.venv\Scripts\python.exe" .\prepare_local.py --reuse-from "C:\Users\Fajar\Downloads\BALANCE_CONNECTED_V2"
& "C:\Users\Fajar\Downloads\BALANCE_INTEGRATED_V1\.venv\Scripts\python.exe" .\show_access.py judge
& "C:\Users\Fajar\Downloads\BALANCE_INTEGRATED_V1\.venv\Scripts\python.exe" .\backend\app.py --host 127.0.0.1 --port 5000
```

Perintah persiapan menyalin artefak model V2 yang sudah ada, memverifikasinya, dan membuat konfigurasi akun untuk folder demo baru. Database, kunci, serta folder asal tidak ditimpa. Jika folder baru sudah disiapkan, gunakan perintah yang sama; model yang sudah lengkap diverifikasi kembali.

Buka http://127.0.0.1:5000. Username/password lokal muncul dari show_access.py judge. Password tersebut bukan password hosting. Simpan untuk tim; jangan unggah config.local.json.

## Alur uji singkat

1. Masuk sebagai juri, klik Hitung rekomendasi.
2. Lihat alokasi tiap zona dan perbandingan proporsional dengan LP.
3. Buka Analitik AI, ubah pasokan menjadi 50%, lalu hitung ulang.
4. Buka Jadwal Irigasi, gunakan kekurangan paket pertama 20%, jalankan replay.
5. Buka Monitoring, klik Kirim telemetri SIMULASI.
6. Buka Riwayat, kemudian Keluar.

Di contoh bawaan, disparitas 5,31 pp menuju 0 pp adalah hasil satu skenario sintetis. Pemenuhan sekitar 28,53% bukan akurasi model. Pasokan nol tidak berarti pemerataan berhasil. Hasil reoptimasi adalah replay terpisah dan tidak membaca sensor miniatur secara otomatis.

## Deploy dan pengumpulan

Ikuti docs/DEPLOY_RENDER.md. Target awal yang disarankan: Render 0.5c-512mb (dulu Starter), satu disk 1 GB, perkiraan US$7,25/bulan sebelum pajak atau pemakaian tambahan.

Pedoman Tahap 2 mewajibkan video 2–5 menit dan tautan desain Figma atau PDF. Aplikasi yang berjalan disertakan sebagai tambahan dengan akun uji/petunjuk singkat. Baca docs/SUBMIT_TAHAP_2.md dan docs/NASKAH_VIDEO.md.

Paket ini belum menghasilkan URL hosting publik. Akun Render, repository tim, password juri produksi, dan pembayaran diselesaikan di akun tim. Tidak ada commit, push, pembelian, atau deployment publik yang dilakukan oleh penyusun paket.
