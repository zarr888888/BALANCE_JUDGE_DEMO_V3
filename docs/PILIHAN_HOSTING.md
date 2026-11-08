# Pilihan hosting untuk demo juri BALANCE

Harga dasar diperiksa 8 November 2026 pada sumber resmi. Harga USD, belum termasuk pajak, kurs pembayaran dan pemakaian tambahan.

| Pilihan | Biaya dasar | Kecocokan |
|---|---|---|
| Render berbayar + disk persisten | Compute 0.5c-512mb US$7/bulan + disk 1 GB US$0,25/bulan, sekitar US$7,25/bulan | Pilihan utama: konfigurasi sudah tersedia, React dan Flask/ML pada satu layanan Docker, SQLite di disk persisten. |
| Railway Hobby + volume | Minimum US$5/bulan termasuk kredit pemakaian US$5; pemakaian lebih ditagih | Alternatif. US$5 bukan jaminan total tetap; biaya mengikuti CPU, RAM, volume dan jaringan. Perlu konfigurasi volume dan environment yang setara. |

Untuk tenggat ini, gunakan Render, satu instance dan disk 1 GB. Tidak perlu frontend berbayar kedua atau migrasi PostgreSQL. Berkas statis React disajikan oleh Flask pada alamat yang sama; SQLite ditempatkan di /var/data/balance. Disk persisten membutuhkan layanan berbayar.

Profil lokal terbaru mencatat puncak RSS 187,69 MiB untuk satu proses yang menjalankan startup, login juri, prediksi/LP, replay, telemetri simulasi dan logout. Ini mendukung pilihan awal 512 MB, tetapi belum membuktikan pemakaian Docker atau beban beberapa pengguna sekaligus. Angka sekitar 155 MiB pada bukti versi sebelumnya memakai cakupan pengukuran yang berbeda; angka tersebut bukan hasil profil V3 ini. Lihat evidence/judge_http_profile.json.

Ikuti docs/DEPLOY_RENDER.md. Periksa total biaya pada dashboard penyedia sebelum mengaktifkan resource berbayar. Paket ini belum di-deploy dan tidak menyertakan URL atau password hosting yang sudah aktif.

## Sumber resmi

- https://render.com/pricing
- https://render.com/docs/compute-plans
- https://render.com/docs/blueprint-spec
- https://render.com/docs/disks
- https://docs.railway.com/pricing/plans
