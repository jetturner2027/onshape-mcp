import server

# Replace with your actual IDs from the test document
DID = "e7a7295f056b7f47b58fb416"
WID = "db250fad1eec3791e9940156"
EID = "51bb41bffd98c6947223a489"

result = server.get_features(DID, WID, EID)

print("\n=== create_box ===")
result2 = server.create_box(DID, WID, EID, width_cm=3, depth_cm=3, height_cm=3, x_cm=5, y_cm=2, z_cm=0)
print(result2)
