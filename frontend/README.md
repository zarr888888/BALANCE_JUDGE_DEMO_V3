# Frontend BALANCE terintegrasi

Baca `../README_MULAI_DI_SINI.md` terlebih dahulu.

- `npm.cmd ci` menyiapkan dependensi sesuai lockfile.
- `npm.cmd run dev` membuka Vite pada 5173, proxy `/api` ke Flask pada 5000.
- `npm.cmd run build` memperbarui `dist` untuk disajikan backend.
- `npm.cmd run lint` memeriksa kode.
- `npm.cmd test` menguji interaksi DOM melawan proses backend nyata pada 5015 menggunakan salinan state sementara. Jalankan setup utama terlebih dahulu. Python tes otomatis memakai `.venv` pada folder utama jika tersedia.

Pengujian DOM memakai JSDOM; tidak membuktikan layout browser atau operasi sensor/aktuator.
