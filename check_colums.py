from pybaseball import statcast_sprint_speed
speed = statcast_sprint_speed(2026, 0)
print(speed.columns.tolist())
print(speed.head())