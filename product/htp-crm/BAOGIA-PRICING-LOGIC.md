# Logic Báo Giá — HTP CRM

Tài liệu này giải thích **toàn bộ cách tính giá** trong hệ thống báo giá, viết bằng
tiếng Việt để dễ đối chiếu và sửa. Code thật nằm ở [pricing.py](pricing.py) (bảng
giá + công thức) và [baogia.py](baogia.py) (xuất file Excel). Sửa số liệu ở đây
xong thì phải cập nhật lại trong `pricing.py` — file này KHÔNG tự động chạy vào hệ
thống, nó chỉ là bản tham chiếu.

> Nguồn gốc: toàn bộ logic được "port" (chuyển) nguyên bản từ công cụ báo giá gốc
> (`customer_form/index.html`). Công thức gốc tính ra đơn vị "nghìn đồng", CRM này
> quy đổi sang **đồng thật (VND)**.
>
> **Cập nhật 2026-07-17:** đối chiếu lại với bản gốc chính xác của công cụ (do
> Hughie cung cấp) và phát hiện bản port trước đó có **mẫu bịa** không tồn tại
> trong bảng giá thật (Cửa Cuốn Đức: KV 412, OT 70, LQ 71, CT 5122, MT 500 R,
> CT 5222 không-R) và **loại cửa bịa** "Nhôm Xingfa" (không có trong công cụ gốc).
> Tất cả đã được sửa lại đúng theo bản gốc — xem chi tiết bên dưới.

---

## 1. Hai loại khách hàng: KH và ĐL

Mọi bảng giá đều có **2 cột giá riêng**:

- **KH** = Khách hàng lẻ (giá bán ra, cao hơn)
- **DL** = Đại lý (giá sỉ, thấp hơn)

Khi lập báo giá, chọn `customer_type` là `"KH"` hoặc `"DL"` — hệ thống tự lấy
đúng bảng giá tương ứng.

---

## 2. Các loại cửa (DOOR_CONFIG)

Hệ thống có **3 loại cửa** (đã bỏ "Nhôm Xingfa" — loại này không có trong công cụ
gốc, là mục bịa thêm), mỗi loại có các trường lựa chọn riêng (loại → mẫu):

### 2.1 Cửa Cuốn (`cua_cuon`)
Chọn **Công nghệ** trước, rồi chọn **Mẫu** tương ứng:

| Công nghệ | Các mẫu |
|---|---|
| Cửa cuốn công nghệ Đức | KV 380, KV 422 R, KV 432 R, KV 468 R, CT 5222 R, MT 5222 R |
| Inox | 6zem, 8zem |
| Cửa cuốn công nghệ Đài Loan | 6zem, 8zem, 1ly |
| Cửa cuốn công nghệ Úc | Tole màu 5.2 zem, Tole màu 5.2 zem blusc |

⚠️ Danh sách mẫu Đức trước đây có thêm KV 412, OT 70, LQ 71, CT 5122, MT 500 R,
CT 5222 (không R) — **không tồn tại trong bảng giá gốc**, đã bị xoá. Nếu những
mẫu này thực sự có bán, cần Hughie xác nhận giá thật rồi mới thêm lại.

### 2.2 Cửa Kéo (`cua_keo`)
Chọn **Loại**: Có lá / Không lá, rồi chọn **Mẫu**: 6zem, 8zem, 1ly, 1.2ly,
1.4ly, 1.6ly.

*(Không đổi — khớp bản gốc.)*

### 2.3 Cửa Nhôm Kính (`nhom_kinh`)
Chọn **Phân loại** trước, rồi chọn **Mẫu** tương ứng — viết lại hoàn toàn theo
bản gốc (trước đây chỉ có Nhôm Việt/Nhôm Nhập với mẫu bịa và KHÔNG có giá):

| Phân loại | Các mẫu |
|---|---|
| Nhôm Việt | Cửa đi 1.2ly, Cửa đi 1.4ly, Cửa đi 2.0ly, Cửa sổ 1.2ly, Cửa sổ 1.4ly, Vách kính 1.2ly, Vách kính 1.4ly |
| Nhôm Nhập | Cửa đi 1.4ly, Cửa đi 2.0ly, Cửa sổ 1.4ly, Vách kính 1.4ly |
| Nhôm Maxpro | Hệ 55, Hệ 65, Hệ 83 |
| Cửa kính bản lề sàn | 10 ly, 12 ly |
| Lan can cầu thang | Tay gỗ, Tay nhôm 3D 2×2, Tay nhôm 3D 3×3, Tay nhôm 3D 4×4, Máng cover |

Tất cả các mẫu trên **đều có giá trong bảng** (mục 3.3) — không còn phải nhập
tay như trước.

---

## 3. Bảng giá theo m² (đơn vị: nghìn đồng/m²)

Mỗi số trong bảng là **giá / m² tính bằng nghìn đồng**. Ví dụ `1450` nghĩa là
1.450.000đ/m².

### 3.1 Cửa Kéo

| Mẫu | Có lá (KH) | Có lá (ĐL) | Không lá (KH) | Không lá (ĐL) |
|---|---|---|---|---|
| 6zem | 640 | 560 | 540 | 480 |
| 8zem | 700 | 620 | 600 | 540 |
| 1ly | 760 | 680 | 660 | 600 |
| 1.2ly | 820 | 740 | 720 | 620 |
| 1.4ly | 900 | 820 | 800 | 660 |

### 3.2 Cửa Cuốn

**Công nghệ Đức**
| Mẫu | KH | ĐL |
|---|---|---|
| KV 380 R | 1.450 | 1.300 |
| KV 422 R | 1.750 | 1.600 |
| KV 432 R | 1.950 | 1.800 |
| KV 468 R | 2.150 | 2.000 |
| CT 5222 R | 2.200 | 2.100 |
| MT 5222 R | 2.300 | 2.200 |

**Inox**: 6zem — KH 1.700 / ĐL 1.600 · 8zem — KH 1.900 / ĐL 1.800

**Công nghệ Đài Loan**: 6zem — KH 500 / ĐL 400 · 8zem — KH 560 / ĐL 460 ·
1ly — KH 780 / ĐL 700

**Công nghệ Úc**: Tole màu 5.2 zem — KH 700 / ĐL 550 · Tole màu 5.2 zem
blusc — KH 900 *(ĐL không có giá riêng cho "blusc" — dùng giá "Tole màu 5.2
zem" thường, hoặc nhập tay nếu khách ĐL yêu cầu đúng loại blusc)*

### 3.3 Cửa Nhôm Kính (mới thêm đầy đủ — trước đây không có)

**Nhôm Việt**
| Mẫu | KH | ĐL |
|---|---|---|
| Cửa đi 1.2ly | 2.200 | 2.000 |
| Cửa đi 1.4ly | 2.400 | 2.200 |
| Cửa đi 2.0ly | 2.600 | 2.400 |
| Cửa sổ 1.2ly | 2.100 | 1.900 |
| Cửa sổ 1.4ly | 2.300 | 2.100 |
| Vách kính 1.2ly | 1.400 | 1.200 |
| Vách kính 1.4ly | 1.500 | 1.300 |

**Nhôm Nhập**
| Mẫu | KH | ĐL |
|---|---|---|
| Cửa đi 1.4ly | 2.600 | 2.400 |
| Cửa đi 2.0ly | 2.800 | 2.600 |
| Cửa sổ 1.4ly | 2.500 | 2.300 |
| Vách kính 1.4ly | 1.700 | 1.500 |

**Nhôm Maxpro**
| Hệ | KH | ĐL |
|---|---|---|
| Hệ 55 | 4.000 | 3.800 |
| Hệ 65 | 4.600 | 4.400 |
| Hệ 83 | 5.800 | 5.600 |

**Cửa kính bản lề sàn**
| Độ dày | KH | ĐL |
|---|---|---|
| 10 ly | 1.500 | 1.300 |
| 12 ly | 1.650 | 1.450 |

**Lan can cầu thang**
| Mẫu | KH | ĐL |
|---|---|---|
| Tay gỗ | 1.950 | 1.750 |
| Tay nhôm 3D 2×2 | 2.050 | 1.850 |
| Tay nhôm 3D 3×3 | 2.300 | 2.100 |
| Tay nhôm 3D 4×4 | 2.600 | 2.400 |
| Máng cover | 5.800 | 5.600 |

### 3.4 Cách sửa giá
Sửa trực tiếp 2 dict `_PRICES_KH` và `_PRICES_DL` trong `pricing.py`. Key có
định dạng:
- Cửa Kéo: `"Cửa Kéo|<Loại> - <Mẫu>"` ví dụ `"Cửa Kéo|Có lá - 6zem"`
- Các loại khác: `"<Phân loại/Công nghệ>|<Mẫu>"` ví dụ
  `"Cửa cuốn công nghệ Đức|KV 380"`, `"Nhôm Maxpro|Hệ 55"`

Nếu thêm mẫu mới, phải thêm **cả 2 chỗ**: option trong `DOOR_CONFIG` (để hiện
lên dropdown) VÀ giá trong `_PRICES_KH`/`_PRICES_DL` (để tính được tiền) —
nếu chỉ thêm 1 chỗ thì mẫu đó sẽ chọn được nhưng không tính ra giá (rơi vào
mục 4, phải nhập tay).

---

## 4. Nhập tay — "Giá đặc biệt" (đơn giá đ/m² × diện tích)

Nhập tay có 2 đường vào, cùng 1 cách tính:

1. **Không khớp bảng giá:** nếu tổ hợp (loại cửa + công nghệ/phân loại + mẫu)
   không khớp key nào, dòng đó bắt buộc nhập tay.
2. **Giá đặc biệt (tùy chọn):** tick ô **"Giá đặc biệt (nhập tay)"** trên từng
   cửa để ép nhập tay **kể cả khi bảng giá CÓ giá** — dùng khi ba muốn tính giá
   riêng (thấp hơn) cho khách quen.

Số nhập vào là **đơn giá (đ/m²)**, hệ thống nhân với diện tích:
`thành_tiền = đơn_giá × ngang_mm × cao_mm / 1_000_000` (`is_manual_price = true`),
**không cộng phụ phí** (cửa nhỏ / Úc). Đây là công thức `pricing.manual_line_total()`,
mirror ở JS preview cả 2 form (wizard Bước 2/3 và form thêm/sửa hạng mục).

Lưu ý: chỉ `thành_tiền` được lưu (DB không có cột đơn giá). Khi **sửa** 1 dòng
nhập tay, đơn giá hiển thị được suy ngược: `đơn_giá = thành_tiền / diện_tích`.

---

## 5. Công thức tính tiền 1 cửa (bảng giá m²)

Với các cửa CÓ giá trong bảng (mục 3), công thức là:

```
Diện tích (m²)  = Ngang(mm) × Cao(mm) / 1.000.000

Thành tiền (đ)  = Giá_bảng(nghìn/m²) × Ngang(mm) × Cao(mm) / 1.000
                  + Phụ phí cửa nhỏ
                  + Phụ phí Úc (nếu có)
```

Ví dụ: Cửa Cuốn Đức KV 380 (giá KH = 1.450), kích thước 3000×2200mm
→ diện tích = 6.6 m² → giá gốc = 1450 × 3000 × 2200 / 1000 = **9.570.000đ**.
Vì 5m² ≤ 6.6m² < 8m² nên có phụ phí cửa nhỏ = 20.000đ/m² × 6.6m² = +132.000đ;
không phải công nghệ Úc nên không có phụ phí Úc → **Thành tiền = 9.702.000đ**.

### 5.1 Phụ phí cửa nhỏ (mọi loại cửa TRỪ Cửa Nhôm Kính)

Dựa vào diện tích m²:

| Diện tích | Phụ phí |
|---|---|
| < 5 m² | +40.000đ / m² |
| 5 m² – < 8 m² | +20.000đ / m² |
| ≥ 8 m² | 0 |

Cửa Nhôm Kính (`nhom_kinh`) **không** áp dụng phụ phí này — mọi phân loại con
của nó (Nhôm Việt/Nhôm Nhập/Nhôm Maxpro/Cửa kính bản lề sàn/Lan can cầu
thang) đều miễn phụ phí cửa nhỏ.

### 5.2 Phụ phí Cửa Cuốn công nghệ Úc

Nếu công nghệ = "Cửa cuốn công nghệ Úc" **và** diện tích < 8 m²:
→ cộng thêm **+600.000đ** (phí cố định, không nhân theo m²).

### 5.3 Đơn giá hiển thị trên báo giá (cột "Đơn giá")

```
Đơn giá/m² hiển thị = Giá_bảng × 1.000 + Phụ_phí_cửa_nhỏ_mỗi_m²
```
(Phụ phí Úc là phí cố định nên không gộp vào đơn giá/m², chỉ cộng thẳng vào
"Thành tiền".)

---

## 6. Phụ kiện (PHUKIEN_CATALOG)

Không đổi trong đợt cập nhật này — khớp bản gốc. 5 phụ kiện có trong danh mục:

1. Moto + rmoc
2. Moto + rmoc Úc
3. Khóa ngang (tole)
4. Khóa ngang (Úc)
5. Bình Tích Điện

Ngoài ra có thể thêm dòng **"chi phí khác"** tự do (tên tự nhập + giá tự nhập),
không nằm trong danh mục cố định.

### 6.1 Motor — tính theo TỪNG bộ Cửa Cuốn

Motor **không** có 1 giá cố định — giá phụ thuộc **diện tích của từng cửa cuốn**
trong báo giá (mỗi cửa cuốn cần 1 bộ moto riêng, tier chọn theo diện tích cửa đó):

| "Moto + rmoc" theo diện tích | Tier |
|---|---|
| < 12 m² | 300kg |
| 12 – < 14.5 m² | 400kg |
| ≥ 14.5 m² | 500kg |

"Moto + rmoc Úc" luôn là 1 tier cố định (không phụ thuộc diện tích).

**Giá motor (full VND):**

| Tier | Giá KH | Giá ĐL |
|---|---|---|
| Moto + rmoc 300kg | 3.300.000 | 3.100.000 |
| Moto + rmoc 400kg | 3.500.000 | 3.300.000 |
| Moto + rmoc 500kg | 4.000.000 | 3.900.000 |
| Moto + rmoc Úc | 4.500.000 | 4.300.000 |

Nếu báo giá có nhiều cửa cuốn + chọn moto cho từng cửa, hệ thống tự tính
tổng theo từng cửa (mỗi cửa 1 bộ, tier riêng theo diện tích cửa đó), rồi gộp
theo tier khi in ra Excel (ví dụ 2 cửa cùng ra tier 300kg thì in 1 dòng SL=2).

### 6.2 Phụ kiện không phải motor — tính 1 lần / báo giá (không nhân diện tích)

| Phụ kiện | Giá KH | Giá ĐL |
|---|---|---|
| Khóa ngang (tole) | 350.000 | 300.000 |
| Khóa ngang (Úc) | 450.000 | 400.000 |
| Bình Tích Điện | 2.200.000 | 2.000.000 |

Số lượng (SL) nhân trực tiếp vào giá (giá × SL).

### 6.3 "Chi phí khác" (tự do)

Dòng tự nhập tên + giá VND, không tra bảng — giá nhập bao nhiêu tính bấy
nhiêu × SL.

### 6.4 Cách sửa giá phụ kiện
Sửa trong `pricing.py`:
- Motor: `_MOTOR_COST_KH`, `_MOTOR_COST_DL`
- Phụ kiện khác: `_ACCESSORY_COST_KH`, `_ACCESSORY_COST_DL`
- Tier diện tích motor: hàm `resolve_motor_label()`

---

## 7. Tổng cộng báo giá (file Excel xuất ra)

```
Cộng tiền hàng   = Tổng "Thành tiền" các cửa  +  Tổng phụ kiện
Thuế VAT (10%)   = round(Cộng tiền hàng × 0.10)
TỔNG CỘNG        = Cộng tiền hàng + Thuế VAT
```

Số tiền cũng được đọc thành **chữ tiếng Việt** ở cuối báo giá (ví dụ
"Mười ba triệu, bốn trăm bốn mươi hai nghìn đồng chẵn") — hàm `doc_so_tien()`.

Với các dòng giá thủ công (mục 4), "Thành tiền" lấy đúng số đã nhập, "Đơn giá"
hiển thị = Thành tiền / Diện tích (chỉ để hiển thị, không dùng để tính lại).

---

## 8. Tóm tắt luồng tính giá 1 dòng cửa

```
1. Có giá trong bảng (mục 3) cho tổ hợp product/công nghệ/mẫu + loại KH?
   CÓ  → Đơn giá/m² = giá bảng × 1.000
         + Phụ phí cửa nhỏ (mục 5.1, trừ Nhôm Kính)
         Thành tiền = Đơn giá/m² × Diện tích + Phụ phí Úc cố định (mục 5.2)
   KHÔNG → Bắt buộc nhập "Thành tiền" thủ công (mục 4)

2. Cộng phụ kiện (mục 6) — motor theo từng cửa cuốn, phụ kiện khác 1 lần.

3. Tổng cộng = (Tổng thành tiền cửa + Tổng phụ kiện) × 1.10 (VAT)
```

---

## 9. File liên quan trong code

| File | Vai trò |
|---|---|
| `pricing.py` | Bảng giá + toàn bộ công thức (single source of truth) |
| `baogia.py` | Xuất báo giá ra file Excel (.xlsx), gọi lại các hàm trong `pricing.py` |
| `app.py` | Tính giá khi lưu báo giá qua form (dòng ~343, ~670) |
| `views.py` | Hiển thị form chọn loại cửa / mẫu / phụ kiện |
| `store.py` | Lưu tổng giá trị báo giá vào DB (dòng ~799) |

⚠️ Bảng `quote_items`/`order_items` trong `store.py` vẫn còn cho phép giá trị
`'xingfa'` ở cấp CHECK constraint (lịch sử cũ) nhưng **không còn dropdown nào
tạo ra giá trị này nữa** — an toàn, không cần sửa schema.
