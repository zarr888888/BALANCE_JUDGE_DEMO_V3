# Akses uji BALANCE — Tahap 2

URL aplikasi: [ISI SETELAH RENDER LIVE]

Akun demo publik tersedia langsung pada panel **Akses Demo Penilaian** di halaman login:
- Username: `juri`
- Password: `BALANCE-Demo2026!`

Gunakan akun ini untuk mencoba prediksi, optimasi, dan replay dengan data SIMULASI. Akun ini memiliki role `judge`, bukan akses operator atau perangkat.

1. Masuk, lalu pilih Hitung rekomendasi pada Dashboard.
2. Tinjau kebutuhan, efisiensi, alokasi, dan rasio pemenuhan zona A/B/C.
3. Buka Analitik AI untuk mengubah pasokan dan menghitung rencana baru.
4. Buka Jadwal Irigasi, gunakan kekurangan 20%, lalu jalankan replay simulasi.
5. Buka Monitoring dan klik Kirim telemetri SIMULASI.
6. Lihat Riwayat dan Keluar.

Model dilatih dengan data sintetis. Nilai volume pada dasbor memakai mL skala demo. Pemenuhan kebutuhan tidak sama dengan akurasi prediksi. Prototipe perangkat IoT didemonstrasikan terpisah; aplikasi publik tidak menggerakkan alat fisik.

Jika login gagal: gunakan akun yang ditampilkan pada halaman login. Untuk hosting, pastikan URL merupakan layanan Render Live. Hubungi tim melalui kontak yang disertakan pada formulir lomba bila masih gagal; jangan menggunakan kunci operator/perangkat.

## Konfigurasi Render untuk tim

Isi environment berikut saat Render disiapkan:

```text
JUDGE_USERNAME=juri
JUDGE_PASSWORD=BALANCE-Demo2026!
ENABLE_ACTUATION=false
```

Pengisian `JUDGE_PASSWORD` pada Render dilakukan manual; `render.yaml` tetap menggunakan `sync: false`. Kredensial demo di atas sengaja publik dan hanya untuk SIMULASI. Rahasia operator/perangkat dan password pribadi tidak boleh dimasukkan ke dokumentasi atau frontend. URL aplikasi tetap placeholder sampai Render aktif.
