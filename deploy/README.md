# Canlıya alma (Hugging Face Spaces)

Uygulama bir Docker imajı olarak herhangi bir sunucuda çalışır (`Dockerfile`). En kolay ve ücretsiz yol
Hugging Face Spaces: model zaten Hugging Face'te, ücretsiz CPU planı 2 çekirdek / 16 GB RAM verir.

## Bir kez yapılacaklar
1. **Space oluştur:** https://huggingface.co/new-space
   - Space name: `sferoid` (istediğin ad)
   - SDK: **Docker** → Blank
   - Hardware: **CPU basic (free)**
   - Public ya da Private (Private seçersen siteyi sadece Space'e eklediğin Hugging Face hesapları açabilir)
2. **Şifre koy:** Space → Settings → *Variables and secrets* → **New secret**
   - `SPHEROID_PASSWORD` = laboratuvara vereceğin şifre (site açılınca tarayıcı sorar; kullanıcı adı önemsiz)
   - Model reposu (`umutovuncsenol/sferoid`) gizliyse ayrıca `HF_TOKEN` = okuma izinli bir token
3. **Yazma token'ı al:** huggingface.co → Settings → Access Tokens → *Write* yetkili token.

## Her güncellemede
```bash
cd ~/spheroid-measure
bash deploy/push_to_space.sh <hf-kullanici-adin>/sferoid
```
Git kullanıcı adı = Hugging Face kullanıcı adın, parola = yazma token'ın. Space kendini yeniden derler
(ilk sefer ~10 dakika). Site adresi: `https://<hf-kullanici-adin>-sferoid.hf.space`

## Bilmen gerekenler
- **Veriler:** Ücretsiz Space'in diski kalıcı değildir; Space yeniden başlarsa (güncelleme, uzun süre
  kullanılmama) yüklenen fotoğraflar ve oturumlar silinir. Excel'i her iş bitince indirin. Kalıcı olsun
  istersen Space → Settings → *Persistent storage* (ücretli, küçük boyut yeter); uygulama oturumları zaten
  `/data/sessions` içine yazar, model önbelleği `/data/hf-cache` içindedir.
- **Uyku:** Ücretsiz Space bir süre kullanılmazsa uyur; ilk açılış bir-iki dakika sürer.
- **Hız:** GPU yok; tespit fotoğraf başına CPU'da birkaç saniyeden birkaç on saniyeye kadar sürebilir.
  Daha hızlı istenirse Settings → Hardware'den GPU seçilebilir (ücretli).
- **Gizlilik:** Fotoğraflar Hugging Face sunucularına yüklenir. Yayınlanmamış deney verisi için
  laboratuvar sorumlusunun onayını alın. Veri dışarı çıkmamalıysa yerel kurulum (install.bat / install.sh)
  aynen çalışmaya devam eder.
- **Tek süreç:** Sunucu tek gunicorn işçisiyle çalışır (oturumlar ve model bellekte); bunu artırmayın.

## Başka bir sunucuda (Render, Railway, Fly.io, kendi sunucunuz)
```bash
docker build -t spheroid-review .
docker run -p 7860:7860 -e SPHEROID_PASSWORD=sifre -v $PWD/sessions:/data/sessions spheroid-review
```
`/data` klasörünü kalıcı bir diske bağlayın.
