# Sumber komponen dan dokumentasi

Kode aplikasi dan core BALANCE di paket ini melanjutkan pekerjaan proyek BALANCE yang telah dikembangkan dalam percakapan. `CORE_ORIGINS.json` mencatat sumber core dan hash sebelum penyesuaian namespace. Tidak ada data mentah mitra disertakan.

Dependensi pihak ketiga dipasang terpisah dan tetap mengikuti lisensinya masing-masing:

- Flask / Pallets: https://flask.palletsprojects.com/ ; pola Blueprint: https://flask.palletsprojects.com/en/stable/blueprints/
- NumPy: https://numpy.org/
- SciPy / HiGHS melalui linprog: https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.linprog.html
- scikit-learn: https://scikit-learn.org/ ; Gradient Boosting dan Ridge dipakai sebagai kandidat regresi.
- joblib: https://joblib.readthedocs.io/
- python-jsonschema: https://python-jsonschema.readthedocs.io/
- Waitress: https://docs.pylonsproject.org/projects/waitress/
- pytest, hanya untuk pengujian: https://docs.pytest.org/
- ESP32 Arduino core / Espressif, contoh HTTPClient: https://github.com/espressif/arduino-esp32/tree/master/libraries/HTTPClient

Daftar ini adalah atribusi komponen; tidak menyatakan dukungan dari pembuat library atau sertifikasi hasil BALANCE.
