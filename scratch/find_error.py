with open("storage/logs/bot.log", "r") as f:
    lines = f.readlines()

found = False
count = 0
for idx, line in enumerate(lines):
    if "could not convert string to float" in line:
        print(f"Match found at line {idx + 1}:")
        # print 10 lines before and 20 lines after
        start = max(0, idx - 5)
        end = min(len(lines), idx + 10)
        for i in range(start, end):
            print(f"{i+1}: {lines[i].strip()}")
        print("-" * 50)
        count += 1
        if count >= 3:
            break
