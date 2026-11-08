# Oracle Cloud'da ücretsiz canlı site

Sonuç: `https://<ip-adresi>.sslip.io` gibi şifreli (HTTPS) ve şifre korumalı, sürekli açık bir site.
Alan adı satın almak gerekmez. Sunucu Oracle'ın "Always Free" ARM makinesidir (2026 itibarıyla
2 çekirdek / 12 GB RAM ücretsiz; uygulamaya yeter).

## A. Hesap (bir kez)
1. https://www.oracle.com/cloud/free/ → **Start for free**.
2. Kart bilgisi doğrulama içindir, ücret çekilmez. Ücretli hesaba ("Upgrade") geçmediğin sürece fatura çıkmaz.
3. **Home region** seçimi sonradan değiştirilemez. Sana yakın bir Avrupa bölgesi seç
   (ör. Frankfurt, Amsterdam, Milano, Stockholm, Marsilya).

## B. Sunucuyu aç
1. Menü → **Compute → Instances → Create instance**.
2. **Name:** `sferoid`
3. **Image and shape → Edit**
   - Image: **Change image → Ubuntu → Canonical Ubuntu 24.04** (aarch64 olan)
   - Shape: **Change shape → Ampere → VM.Standard.A1.Flex**, **OCPU: 2**, **Memory: 12 GB**
   - "Always Free-eligible" etiketi görünmeli. Görünmüyorsa değerleri küçült.
4. **Networking:** "Create new virtual cloud network" ve **Assign a public IPv4 address = Yes**.
5. **Add SSH keys:** **Generate a key pair for me** → **Save private key** (dosyayı kaybetme!).
6. **Boot volume:** varsayılan (≈50 GB) kalsın.
7. **Create**. Durum **Running** olunca sayfadaki **Public IP address**'i not et.
   - "Out of capacity" hatası alırsan: birkaç saat sonra tekrar dene ya da
     Placement'ta başka bir **Availability domain** seç. Bu Oracle tarafında sık görülen bir durum.

## C. Ağda 80 ve 443 portlarını aç (Oracle tarafı)
1. Instance sayfasında **Primary VNIC → Subnet** bağlantısına tıkla.
2. **Security → Security Lists → Default Security List** → **Add Ingress Rules**:
   - Source CIDR: `0.0.0.0/0`, IP Protocol: **TCP**, Destination Port Range: `80,443` → **Add**.

## D. Sunucuya bağlan ve kur (Mac terminali)
```bash
chmod 400 ~/Downloads/ssh-key-*.key
ssh -i ~/Downloads/ssh-key-*.key ubuntu@<PUBLIC_IP>
```
("Are you sure you want to continue connecting" sorarsa `yes`.) Sunucudayken:
```bash
git clone https://github.com/umutovuncsenol/spheroid-measure.git
bash spheroid-measure/deploy/oracle/setup.sh
```
Betik site şifresini sorar (laboratuvara vereceğin şifre) ve adres önerir (Enter ile kabul et).
İlk kurulum 5–15 dakika sürer. Sonunda site adresini yazar: `https://<ip>.sslip.io`

## Güncelleme (GitHub'a yeni kod gönderdikten sonra)
```bash
ssh -i ~/Downloads/ssh-key-*.key ubuntu@<PUBLIC_IP>
bash spheroid-measure/deploy/oracle/update.sh
```
Oturumlar ve fotoğraflar `~/sferoid-data` klasöründe durur; güncelleme ve yeniden başlatmada silinmez.

## Bilmen gerekenler
- **Kayıtlar:** `sudo docker compose -f ~/spheroid-measure/deploy/oracle/docker-compose.yml logs -f app`
- **Şifreyi değiştirmek:** `setup.sh`'ı tekrar çalıştır.
- **Boşta sunucu:** Oracle, 7 gün boyunca işlemci, ağ ve bellek kullanımı %20'nin altında kalan ücretsiz
  sunucuları geri alabileceğini söylüyor. Model bellekte yüklü durduğu için bellek kullanımı genelde bu
  sınırın üzerindedir; yine de önemli verileri (Excel'ler) düzenli indirin.
- **Hız:** GPU yok; tespit CPU'da fotoğraf başına birkaç saniye ile birkaç on saniye sürebilir.
- **Gizlilik:** Fotoğraflar Oracle'ın (senin seçtiğin bölgedeki) sunucusunda durur. Laboratuvar
  sorumlusunun onayını alın.
