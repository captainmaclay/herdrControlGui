import tkinter as tk
import config_app

app = config_app.HerdrConfigApp()
app.after(500, app._turn_off_node_isolate_rules)
app.after(2000, app.destroy)
app.mainloop()
