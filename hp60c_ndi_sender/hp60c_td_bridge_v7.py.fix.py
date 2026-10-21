import re

with open(r"D:\项目\交互艺术\书法微更新\hp60c_ndi_sender\hp60c_td_bridge_v7.py", "r", encoding="utf-8") as f:
    content = f.read()

# Fix 1: undistort_depth method
old1 = """    def undistort_depth(self, depth):
        return cv2.remap(depth, self.map_depth_x, self.map_depth_y, cv2.INTER_NEAREST)"""
new1 = """    def undistort_depth(self, depth):
        import cv2
        return cv2.remap(depth, self.map_depth_x, self.map_depth_y, cv2.INTER_NEAREST)"""
content = content.replace(old1, new1)

# Fix 2: align_depth_to_rgb method
old2 = """    def align_depth_to_rgb(self, depth):
        \"\"\"将深度图对齐到 RGB 像素坐标系\"\"\"
        depth_undist = self.undistort_depth(depth)"""
new2 = """    def align_depth_to_rgb(self, depth):
        \"\"\"将深度图对齐到 RGB 像素坐标系\"\"\"
        import cv2
        depth_undist = self.undistort_depth(depth)"""
content = content.replace(old2, new2)

# Fix 3: Also fix _init_undistort_maps and _init_projection_matrices
old3 = """    def _init_undistort_maps(self):
        self.map_rgb_x, self.map_rgb_y = cv2.initUndistortRectifyMap(
            self.K_rgb, self.dist_rgb, None, self.K_rgb,
            (self.width, self.height), cv2.CV_32FC1
        )
        self.map_depth_x, self.map_depth_y = cv2.initUndistortRectifyMap(
            self.K_depth, self.dist_depth, None, self.K_depth,
            (self.width, self.height), cv2.CV_32FC1
        )"""
new3 = """    def _init_undistort_maps(self):
        import cv2
        self.map_rgb_x, self.map_rgb_y = cv2.initUndistortRectifyMap(
            self.K_rgb, self.dist_rgb, None, self.K_rgb,
            (self.width, self.height), cv2.CV_32FC1
        )
        self.map_depth_x, self.map_depth_y = cv2.initUndistortRectifyMap(
            self.K_depth, self.dist_depth, None, self.K_depth,
            (self.width, self.height), cv2.CV_32FC1
        )"""
content = content.replace(old3, new3)

old4 = """    def _init_projection_matrices(self):
        u, v = np.meshgrid(
            np.arange(self.width, dtype=np.float32),
            np.arange(self.height, dtype=np.float32)
        )
        Kd_inv = np.linalg.inv(self.K_depth)
        ones = np.ones_like(u)
        uv1 = np.stack([u, v, ones], axis=-1)
        X_depth_norm = uv1 @ Kd_inv.T
        self.Xn_x = X_depth_norm[..., 0]
        self.Xn_y = X_depth_norm[..., 1]

        self.KR = (self.K_rgb @ self.R_d2r).astype(np.float32)
        self.KT = (self.K_rgb @ (self.T_d2r_m / 1000.0)).astype(np.float32).ravel()"""
new4 = """    def _init_projection_matrices(self):
        import cv2
        u, v = np.meshgrid(
            np.arange(self.width, dtype=np.float32),
            np.arange(self.height, dtype=np.float32)
        )
        Kd_inv = np.linalg.inv(self.K_depth)
        ones = np.ones_like(u)
        uv1 = np.stack([u, v, ones], axis=-1)
        X_depth_norm = uv1 @ Kd_inv.T
        self.Xn_x = X_depth_norm[..., 0]
        self.Xn_y = X_depth_norm[..., 1]

        self.KR = (self.K_rgb @ self.R_d2r).astype(np.float32)
        self.KT = (self.K_rgb @ (self.T_d2r_m / 1000.0)).astype(np.float32).ravel()"""
content = content.replace(old4, new4)

with open(r"D:\项目\交互艺术\书法微更新\hp60c_ndi_sender\hp60c_td_bridge_v7.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Fixes applied successfully!")