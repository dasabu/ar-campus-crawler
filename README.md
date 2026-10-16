# Lấy ảnh các cảnh trong VinUni virtual tour và ghép cubemap

Đầu ra giống cảnh Library Reception đã làm: mỗi điểm chụp có sáu ảnh
`f.jpg`, `b.jpg`, `l.jpg`, `r.jpg`, `u.jpg`, `d.jpg`.
Đây là ảnh từ một tâm chụp nhìn sáu hướng, không phải bản đồ 3D.
Phạm vi là các cảnh được công bố trong virtual tour, không đảm bảo mọi phòng
hay mọi vị trí của campus đều được tour bao phủ.

## 1. Cài đặt trên macOS / Linux

Giải nén bộ code, mở Terminal và cd vào thư mục `campusar-tour-kit`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
curl --version
```

Cần Python 3.10 trở lên. macOS thường có sẵn curl.
Code dùng curl để tải public image URL, Pillow để ghép ảnh; không gọi imagegen.

## 2. Cách nhanh: xuất metadata của tất cả scene mà viewer đã nạp

1. Mở https://vinuni.edu.vn/virtual-tour/index.html bằng Chrome.
2. Đợi một cảnh thật sự hiện ra, không còn màn hình xám.
3. Mở DevTools bằng Option + Command + I, chọn **Console**.
4. Mở `export_scenes.js` bằng editor, đọc rồi copy toàn bộ nội dung vào Console.
5. Chạy code. Chrome tải file `vinuni-scenes.json`; chép file này vào thư mục code.

Script chỉ đọc API công khai `scene.count`, `scene[i].name`,
`scene[i].title`, `scene[i].content`, `xml.url` của viewer `bnhb`, rồi xuất JSON.
Nó không giải mã `project.xml`, không thay đổi tour, không truy cập tài khoản.
Các thuộc tính này có trong tài liệu API/XML của krpano.
Script không cần xoay camera hoặc tải ảnh của từng cảnh vào trình duyệt.

Nếu báo không tìm thấy `bnhb`: đợi tour tải, chọn đúng frame của trang trong
Console. Nếu API không được site expose, dùng cách HAR ở phần 5.
Đừng cài extension hay sửa bảo mật trình duyệt chỉ để chạy bước này.

Kiểm kê trước, chưa tải ảnh:

```bash
python tour_tiles.py --manifest vinuni-scenes.json --dry-run --out vinuni-output
```

Xem số scene/tile và `vinuni-output/plan.json`.
Mặc định mỗi scene chọn mức có kích thước ảnh lớn nhất trong cấu hình của nó.
**Không mặc định l2 hay 5x5 cho mọi scene**: mức ảnh và lưới thay đổi theo cảnh.
Nếu `skipped_scenes` khác rỗng, đọc nguyên nhân. Không coi các scene bị skip
là đã tải thành công. Bộ code hỗ trợ cubemap multires dạng long syntax
`<image><level><cube url=.../></level></image>` với tile không chồng biên.
Short syntax, stereo, custom cube labels, external includes chưa expand, và
URL có biến chưa resolve sẽ được báo để chuyển sang HAR, không đoán URL.

Thử một scene trước (thay `a1` bằng tên chính xác mà bước kiểm kê in ra):

```bash
python tour_tiles.py --manifest vinuni-scenes.json --scene a1 --out vinuni-output
```

Metadata có thể đặt tên scene khác mã ảnh trong URL, chẳng hạn `scene_a1`.
Khi dùng `--manifest`, `--scene` nhận **name trong manifest**.

Nếu ảnh đúng, tải toàn bộ các scene được hỗ trợ:

```bash
python tour_tiles.py --manifest vinuni-scenes.json --out vinuni-output --workers 4
```

Không bắt buộc `--scene`: bỏ cờ đó nghĩa là xử lý tất cả scene có trong manifest.
Nếu muốn mức 2, dùng `--level 2`; mức 2 có thể không tồn tại ở mọi scene.
Scene tên/mức bị thiếu cần xem trong report và danh mục gốc.

## 3. Ảnh nằm ở đâu?

```text
vinuni-output/
  plan.json
  report.json
  tiles/
    a1/l2/f/1_1.jpg
    a1/l2/f/1_2.jpg
    ...
  faces/
    a1/l2/f.jpg
    a1/l2/b.jpg
    a1/l2/l.jpg
    a1/l2/r.jpg
    a1/l2/u.jpg
    a1/l2/d.jpg
```

`f/b/l/r/u/d` là front/back/left/right/up/down. Sáu file là sáu mặt cubemap.
Script ghép tile theo hàng/cột, giữ riêng các mức phân giải, crop phần padding
ở mép theo kích thước metadata, và không bù các ô thiếu bằng nội dung giả.
Đây không phải ảnh equirectangular 2:1. Muốn dạng đó cần thêm bước chiếu
cubemap sang equirectangular; không nối ngang sáu file để giả panorama.

Kiểm tra `report.json`:

- `scenes[].complete_six_faces: true`: đã tạo đủ sáu mặt ở mức được chọn.
- `faces[].status: incomplete`: thiếu hoặc hỏng tile; xem `missing`.
- `download_errors`: lỗi tải; code không tự vượt 403 hoặc lấy cookie từ HAR.
- `skipped_scenes`: scene không đọc được/định dạng chưa hỗ trợ.

Chạy lại cùng lệnh để tiếp tục. Tile đã có và đọc được sẽ được tái sử dụng.
Mã thoát 0 = mọi scene được xử lý đầy đủ; mã 2 = có lỗi, thiếu hoặc skip.
Report của lần chạy hiện tại là nguồn kiểm tra kết quả; ảnh từ lần chạy cũ
có thể vẫn nằm trong thư mục. Dùng `--out` khác cho bộ dữ liệu khác.
Để chỉ ghép lại tile đã tải mà không truy cập mạng:

```bash
python tour_tiles.py --manifest vinuni-scenes.json --out vinuni-output --merge-only
```

## 4. Kiểm tra đã lấy hết các scene được công bố chưa

Xuất metadata chỉ đảm bảo danh sách scene **viewer đã nạp**, không tự chứng minh
toàn bộ tour đã có trong đó. Đối chiếu menu từng nhóm khu vực với scene trong
manifest và report. Một menu có thể không phải một điểm chụp, và một địa điểm
có thể có nhiều scene. Nếu chuyển nhóm làm viewer nạp XML khác, xuất manifest
lần nữa và chạy với `--out` riêng để tránh trùng mã scene giữa các tour con.

Tạo bảng thủ công gồm khu vực, tên hiển thị, mã scene, trạng thái tải,
và vị trí thực tế. API có thể không chứa title mà menu hiển thị; khi đó điền
tên từ menu. Không suy ra tên phòng hoặc tọa độ từ mã `a1`, `a2`, ...

## 5. Cách dự phòng: HAR như Reception hôm trước

1. Mở DevTools > Network, bật **Preserve log** và **Disable cache**.
2. Chọn Img; có thể thêm filter `/panos/`.
3. Xóa log một lần trước khi bắt đầu, sau đó reload trang để ghi lại ảnh.
4. Mở các scene, xoay quanh 360 độ, nhìn lên/xuống, và zoom. Giữ DevTools mở.
5. Export theo từng nhóm (Library, Dormitory...) để file không quá lớn:
   chuột phải request > Copy > Save all listed as HAR (sanitized),
   hoặc nút Export HAR (sanitized) trên thanh công cụ Network.
6. Giữ file trong thư mục `hars/`.

HAR chỉ ghi những request xảy ra; có thể thiếu tile chưa nhìn tới. Kích thước
lưới không thể xác định chắc chắn chỉ bằng chỉ số lớn nhất xuất hiện trong HAR.
Vì vậy code **không tự đánh dấu một mặt là đầy đủ** dựa vào lưới quan sát được.

Kiểm kê các file:

```bash
python tour_tiles.py --har hars/*.har --dry-run --out har-output
```

Không biết lưới: có thể tải các tile đã quan sát, nhưng chưa ghép:

```bash
python tour_tiles.py --har hars/*.har --out har-output
```

Đối với riêng `a1/l2`, hôm trước đã xác nhận lưới 5x5:

```bash
python tour_tiles.py --har hars/vinuni-a1.har --scene a1 --level 2 --grid 5x5 --out har-output
```

Nếu đã tải rồi, chỉ ghép:

```bash
python tour_tiles.py --har hars/vinuni-a1.har --scene a1 --level 2 --grid 5x5 --out har-output --merge-only
```

Với scene khác, đọc kích thước level/tile từ metadata rồi tính:

```text
cols = ceil(tiledimagewidth / tilesize)
rows = ceil(tiledimageheight / tilesize)
```

Ưu tiên manifest nếu tile mép có padding: HAR không có kích thước đầy đủ
để biết phần nào phải crop. `--grid` chỉ áp dụng cho các scene có đúng cùng
lưới đó; chia thành các lệnh theo scene khi kích thước khác nhau.

Nếu HAR không ghi đủ tile, code chỉ báo thiếu chứ không tự thử hàng loạt
đường dẫn ngoài danh sách. Quay lại scene, tải phần còn thiếu và export thêm
HAR; truyền tất cả HAR vào cùng lệnh. HAR có ảnh base64 thì script dùng ảnh
nhúng; HAR chỉ có URL thì script tải URL công khai, không dùng auth/cookie.

## 6. Kiểm tra code và giới hạn xác minh

```bash
python -m unittest -v
```

Các test offline kiểm tra URL padding, kích thước từ metadata, đúng thứ tự
hàng/cột, crop tile mép, và phát hiện thiếu tile. Parser HAR đã kiểm tra với
file `vinuni-a1.har` thật: 150 URL ở a1/l2, sáu mặt, mỗi mặt 25 tile.
Xuất scene bằng Console dựa trên API chính thức; chưa xác minh trực tiếp
mọi cấu hình scene của tour VinUni. Hãy thử một scene rồi xem report trước
khi tải cả bộ. Chỉ dùng dữ liệu công khai; với việc tái phân phối hoặc công bố
dataset, xác nhận quyền sử dụng ảnh với đơn vị quản lý tour.

## Tài liệu tham khảo

- https://krpano.com/docu/js/
- https://krpano.com/docu/xml/#scene.content
- https://krpano.com/docu/xml/#image
- https://developer.chrome.com/docs/devtools/network/reference/
