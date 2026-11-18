# first ver рулетки (really eazy)
 
# from random import choice; print("| " + " | ".join([choice('🍒🍋🍏🍊') for _ in range(3)]) + " |")
 
# print()



# second ver рулетки (eazy)

# import random
# from pyfiglet import Figlet
# import time
# for i in range(40):
#     a, b, c = choice('🍒🍋🍏🍊'), choice('🍒🍋🍏🍊'), choice('🍒🍋🍏🍊')
#     print(f"\r| {a} | {b} | {c} |  ", end="", flush=True)
#     time.sleep(random.choice([0.2, 0.3, 0.4]))
# print()
# if a == b == c: f = Figlet(font="slant"); print(f.renderText("WIN"))
# else: f = Figlet(font="slant"); print(f.renderText("LOOSE"))


# therd ver рулетки (eazy too)

# import random
# from pyfiglet import Figlet
# import time
# answer = input(f"Приветствую тебя! Хочешь сыграть?  "); print()
# while answer.lower() in ['да', 'yes', 'da']:
#     for i in range(20):
#         a, b, c = choice('🍒🍋🍏🍊'), choice('🍒🍋🍏🍊'), choice('🍒🍋🍏🍊')
#         print(f"\r| {a} | {b} | {c} |  ", end="", flush=True)
#         time.sleep(random.choice([0.2, 0.3, 0.4]))
#     print()
# print()
# print("Досвидания")



import arcade
from arcade.gui import UIManager, UITextureButton


class Rulletka(arcade.Window):
    def __init__(self, WIDTH, HEIGHT):
        super().__init__(WIDTH, HEIGHT, "Рулетка")

        self.width = WIDTH
        self.height = HEIGHT

        self.update_rate = 1 / 60
        self.set_update_rate(self.update_rate)

        self.active_texture = arcade.load_texture("assets/btn_active.png")
        self.hover_texture = arcade.load_texture("assets/btn_hovered.png")
        self.pressed_texture = arcade.load_texture("assets/btn_pressed.png")

        self.btn = UITextureButton(text="Играть", width=200, height=50,
                                   x=self.width // 2 - 100,
                                   y=self.height // 2 - 25,
                                   text_color=arcade.color.BLACK,
                                   texture=self.active_texture,
                                   hover_texture=self.hover_texture,
                                   pressed_texture=self.pressed_texture)

        self.btn.on_click = self.on_click

        self.background_color = arcade.color.LIGHT_GREEN

        self.ui_manager = UIManager()
        self.ui_manager.add(self.btn)


    def on_draw(self):
        self.clear()

        self.ui_manager.draw()

    def update(self, delta_time):
        pass

    def on_click(self, event):
        pass

if __name__ == "__main__":
    app = Rulletka(800, 600)
    arcade.run()
