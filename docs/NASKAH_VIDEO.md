# Naskah kerja video BALANCE — target sekitar 4 menit

Sesuaikan nama anggota dan rekaman yang benar-benar tersedia. Urutan mengikuti pedoman. Gunakan hasil percobaan yang direkam; angka contoh di bawah bukan jaminan semua skenario.

## 00:00–00:45 · Permasalahan dan bukti kebutuhan

“Pembagian air irigasi perlu mempertimbangkan kebutuhan hulu, tengah, dan hilir ketika pasokan terbatas. Dalam wawancara di wilayah kepengamatan Sindang, mitra menjelaskan adanya hambatan saluran dan belum tersedianya rekap histori kekurangan yang lengkap. Temuan ini menjadi dasar kami merancang BALANCE sebagai alat bantu perencanaan pembagian air.”

Visual: tim, lokasi/wawancara yang telah diizinkan, dan ringkasan masalah. Atribusikan temuan kepada wawancara; jangan menambahkan frekuensi, luas terdampak, atau angka kerugian yang belum diukur.

## 00:45–01:20 · Solusi dan alasan

“BALANCE menggabungkan prediksi dan optimasi. Machine learning memperkirakan kebutuhan, pasokan, dan efisiensi saluran. Linear Programming kemudian menghitung alokasi dengan memperhatikan satu pasokan bersama, kapasitas saluran, dan perlindungan hilir. Tujuannya menyeimbangkan tingkat pemenuhan kebutuhan, sehingga setiap zona tidak harus menerima volume yang sama.”

Visual: tiga zona dan alur prediksi → alokasi → jadwal.

## 01:20–02:50 · Pengguna, alur utama, dan fitur

“Pengguna sasaran kami adalah pengelola distribusi irigasi. Pada dasbor, pengguna mengatur skenario pasokan dan kondisi zona, kemudian menghitung rekomendasi. Hasilnya memperlihatkan kebutuhan, efisiensi, volume alokasi, perkiraan penerimaan, serta rasio pemenuhan tiap zona.”

Rekam: masuk akun juri → Dashboard → Hitung rekomendasi → tabel dan grafik → Analitik AI ubah pasokan → Jadwal Irigasi → replay kekurangan 20% → Monitoring simulasi → Riwayat.

“Pada contoh sintetis yang ditampilkan, disparitas pembagian proporsional sebesar sekitar 5,31 poin persentase menjadi sekitar nol setelah optimasi LP. Ini hasil skenario simulasi tersebut, bukan bukti pemerataan lapangan. Angka pemenuhan sekitar 28,53 persen juga bukan akurasi model.”

Jika angka pada rekaman berbeda, narasikan angka yang benar-benar terlihat atau hapus kalimat angka.

“Replay menguji respons perencanaan terhadap gangguan penerimaan dalam simulator. Data monitoring pada demo publik diberi label SIMULASI.”

## 02:50–03:40 · Teknologi dan peran AI

“Model regresi yang digunakan adalah Gradient Boosting untuk kebutuhan dan efisiensi, serta Ridge untuk pasokan. Prediksi menjadi koefisien masukan optimasi LP; scheduler menyusun urutan dan waktu distribusi. Backend memakai Flask dan frontend React. Akun juri dapat mencoba fungsi simulasi melalui aplikasi yang disediakan.”

“Tim juga membangun prototipe miniatur ESP32. Pada tahap ini perangkat didemonstrasikan secara mandiri dan integrasi fisik menyeluruh masih dikembangkan. Model AI saat ini dilatih pada data sintetis; validasi dengan data pengukuran merupakan pekerjaan berikutnya.”

Visual: cuplikan perangkat yang benar-benar berfungsi; beri keterangan “prototipe IoT mandiri” bila dijalankan oleh firmware terpisah.

## 03:40–04:00 · Penutup

“BALANCE menyediakan dasar perencanaan distribusi yang dapat ditinjau melalui alokasi, penjelasan, dan jadwal. Pada pengembangan berikutnya kami melengkapi pengukuran, kalibrasi, dan integrasi perangkat bersama mitra. Terima kasih.”

Tampilkan identitas tim, sumber komponen, serta URL demo dan petunjuk akses yang disediakan pada formulir. Hindari merekam kunci operator, kunci IoT, password hosting, atau data mentah mitra.
