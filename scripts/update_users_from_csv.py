

def parse_mapping(text):
    mapping = {}
    for line in text.strip().splitlines():
        parts = line.strip().split()
        #!/usr/bin/env python3
        """Update users JSON with kitchen_available and main_course_possible from
        space/tab-separated rows provided inside this script.

        Usage:
          python3 scripts/update_users_from_csv.py /path/to/dinnerhopping.users.json

        The script creates a timestamped backup next to the target JSON.
        """
        import sys
        import json
        import shutil
        from pathlib import Path
        from datetime import datetime

        CSV_TEXT = '''
        1	Anna Schmidt		w	anna.s@email.de			Bahnhofstraße 5, 37073 Göttingen	Informatik		Vegetarisch	Laktoseintoleranz	Hauptspeise	Ja	Ja
        2	Ben Weber	Clara Fischer	m	ben.weber@email.de	w	clara.fischer@email.de	Goßlerstraße 19, 37073 Göttingen	Jura	Medizin	Alles		Egal	Ja	Ja
        3	David Meier		m	david.m@email.de			Weender Landstraße 80, 37075 Göttingen	BWL		Vegan	Nussallergie	Vorspeise	Ja	Ja
        4	Eva Klein	Felix Lang	w	eva.klein@email.de	m	felix.lang@email.de	Grätzelstraße 12, 37079 Göttingen	Medizin	Physik	Alles		Dessert	Ja	Ja
        5	Greta Hoffmann		w	greta.h@email.de			Danziger Straße 2, 37083 Göttingen	Soziologie		Vegetarisch		Egal	Ja	Ja
        6	Hans Zimmer	Ida Schulz	m	hans.zimmer@email.de	w	ida.schulz@email.de	Am Papenberg 1, 37075 Göttingen	Physik	Chemie	Alles	Glutenfrei	Hauptspeise	Ja	Ja
        7	Jonas Wolf		m	jonas.w@email.de			Robert-Koch-Straße 40, 37075 Göttingen	Chemie		Alles		Vorspeise	Nein	Nein
        8	Kira Brand	Leo Roth	w	kira.brand@email.de	m	leo.roth@email.de	Tulpenweg 9, 37085 Göttingen	Anglistik	Geschichte	Vegetarisch		Dessert	Ja	Ja
        9	Max Neumann		d	max.n@email.de			Königsallee 23, 37083 Göttingen	Wirtschaftsinformatik		Alles		Egal	Ja	Ja
        10	Nora Beck	Otto Hahn	w	nora.beck@email.de	m	otto.hahn@email.de	Humboldtallee 11, 37073 Göttingen	Geschichte	Physik	Vegan		Vorspeise	Ja	Nein
        11	Paula Sommer		w	paula.s@email.de			Bühlstraße 22, 37073 Göttingen	Psychologie		Alles		Hauptspeise	Ja	Ja
        12	Quintus Voss	Rosa Berg	m	quintus.voss@email.de	w	rosa.berg@email.de	Geismar Landstraße 55, 37083 Göttingen	Kunstgeschichte	Philosophie	Vegetarisch	Sellerie	Dessert	Ja	Ja
        13	Sofia Wagner		w	sofia.w@email.de			Bürgerstraße 50, 37073 Göttingen	Politikwissenschaft		Alles		Egal	Ja	Ja
        14	Tom Richter	Lina Koch	m	tom.richter@email.de	w	lina.koch@email.de	Nikolausberger Weg 7, 37073 Göttingen	Alles		Hauptspeise			Ja	Ja
        15	Ursula Meyer		w	ursula.m@email.de			Schillerstraße 44, 37083 Göttingen	Germanistik		Vegetarisch		Vorspeise	Ja	Ja
        16	Viktor Bauer	Zoe Schröder	m	viktor.b@email.de	w	zoe.schroeder@email.de	Reinhäuser Landstraße 91, 37083 Göttingen	Agrarwissenschaften	Geographie	Alles		Egal	Ja	Ja
        17	Walter Schwarz		m	walter.s@email.de			Theodor-Heuss-Straße 21, 37075 Göttingen	Sportwissenschaft		Alles	Fischallergie	Dessert	Nein	Nein
        18	Xenia Zimmermann		d	xenia.z@email.de			Düstere-Eichen-Weg 60, 37073 Göttingen	Philosophie		Vegan		Vorspeise	Ja	Ja
        19	Yannick Becker	Amelie Schäfer	m	yannick.b@email.de	w	amelie.schaefer@email.de	Kiesseestraße 33, 37083 Göttingen	Alles		Egal			Ja	Ja
        20	Lena Keller		w	lena.k@email.de			Goethe-Allee 13, 37073 Göttingen	Jura		Vegetarisch		Hauptspeise	Ja	Ja
        21	Moritz Horn	Charlotte Graf	m	moritz.horn@email.de	w	charlotte.graf@email.de	Zindelstraße 3, 37073 Göttingen			Alles	Laktoseintoleranz	Dessert	Ja	Ja
        22	Elias Voigt		m	elias.v@email.de			Petrikirchstraße 9, 37077 Göttingen	Physik		Alles		Egal	Ja	Ja
        23	Hannah Kraus	Julian Lorenz	w	hannah.k@email.de	m	julian.lorenz@email.de	Groner-Tor-Straße 31, 37073 Göttingen			Vegetarisch		Vorspeise	Ja	Ja
        24	Noah Stein		m	noah.s@email.de			Papendiek 16, 37073 Göttingen	Informatik		Alles		Hauptspeise	Ja	Ja
        25	Lara Huber	Finn Pohl	w	lara.huber@email.de	m	finn.pohl@email.de	Kurze-Geismar-Straße 20, 37073 Göttingen			Alles		Egal	Ja	Ja
        26	Emil Seidel		m	emil.s@email.de			Obere Karspüle 22, 37073 Göttingen	Geschichte		Vegan	Glutenfrei	Vorspeise	Ja	Ja
        27	Marie Pohl	Lukas Engel	w	marie.pohl@email.de	m	lukas.engel@email.de	Rosdorfer Weg 1, 37073 Göttingen			Alles		Dessert	Ja	Ja
        28	Paul Werner		m	paul.w@email.de			Theaterplatz 7, 37073 Göttingen	Soziologie		Vegetarisch		Vorspeise	Nein	Nein
        29	Mia Brandt	Leon Herrmann	w	mia.brandt@email.de	m	leon.herrmann@email.de	Untere-Masch-Straße 10, 37073 Göttingen			Alles		Egal	Ja	Ja
        30	Felix Wunderlich		d	felix.w@email.de			Am Weißen Steine 8, 37085 Göttingen	Biologie		Alles		Vorspeise	Ja	Ja
        31	Julia Peters	Tim Kramer	w	julia.peters@email.de	m	tim.kramer@email.de	Auf dem Greite 15, 37083 Göttingen			Vegetarisch		Dessert	Ja	Ja
        32	Niklas Franke		m	niklas.f@email.de			Im Hassel 2, 37077 Göttingen	Sportwissenschaft		Alles		Egal	Ja	Ja
        33	Sarah Berger	David Roth	w	sarah.berger@email.de	m	david.roth@email.de	An der Lutter 22, 37075 Göttingen			Alles	Nussallergie	Vorspeise	Ja	Nein
        34	Laura Haas		w	laura.h@email.de			Nonnenstieg 1, 37075 Göttingen	Philosophie		Vegan		Vorspeise	Ja	Ja
        35	Anton Schubert	Ida Simon	m	anton.schubert@email.de	w	ida.simon@email.de	Greifswalder Straße 5, 37085 Göttingen			Alles		Egal	Ja	Ja
        36	Klara Ludwig		w	klara.l@email.de			Herzberger Landstraße 100, 37085 Göttingen	Psychologie		Vegetarisch	Fruktoseintoleranz	Dessert	Ja	Ja
        37	Maximilian Böhm	Lea Otto	m	max.boehm@email.de	w	lea.otto@email.de	Göttinger Straße 4, 37073 Göttingen			Alles		Hauptspeise	Ja	Ja
        38	Lisa Langhans		w	lisa.l@email.de			Am Gartetalbahnhof 1, 37073 Göttingen	Germanistik		Alles		Egal	Ja	Ja
        39	Oskar Friedrich	Mathilda Vogel	m	oskar.f@email.de	w	mathilda.vogel@email.de	Stettiner Straße 12, 37083 Göttingen			Vegetarisch		Vorspeise	Ja	Ja
        40	Johanna Keller		w	johanna.k@email.de			Von-Bar-Straße 2, 37075 Göttingen	Medizin		Alles		Dessert	Ja	Ja
        41	Timon Gärtner	Luisa Ernst	m	timon.g@email.de	w	luisa.ernst@email.de	Christophorusweg 7, 37075 Göttingen			Alles		Egal	Ja	Ja
        42	Amelie Fuchs		w	amelie.f@email.de			Mittelberg 19, 37085 Göttingen	Soziologie		Vegan		Hauptspeise	Ja	Ja
        43	Jan Winter	Sophie Sommer	m	jan.winter@email.de	w	sophie.sommer@email.de	Am Feuerschanzengraben 14, 37073 Göttingen			Alles		Vorspeise	Ja	Ja
        44	Carla Jung		w	carla.j@email.de			Lohberg 7, 37073 Göttingen	Kunstgeschichte		Vegetarisch		Egal	Ja	Ja
        45	Florian Grimm	Nele Schreiber	m	florian.g@email.de	w	nele.schreiber@email.de	Asternweg 5, 37085 Göttingen			Alles		Dessert	Ja	Ja
        46	Pia Linke		w	pia.l@email.de			Stegemühlenweg 55, 37083 Göttingen	Biologie		Alles	Glutenfrei	Hauptspeise	Ja	Ja
        47	Simon Jäger	Elisa Kurz	m	simon.j@email.de	w	elisa.kurz@email.de	Wilhelm-Weber-Straße 20, 37073 Göttingen			Vegetarisch		Egal	Ja	Ja
        48	Theresa May		w	theresa.m@email.de			Platz der Göttinger Sieben 5, 37073 Göttingen	Politikwissenschaft		Alles		Vorspeise	Ja	Ja
        49	Adrian Muth	Isabell Hahn	m	adrian.m@email.de	w	isabell.hahn@email.de	Windausweg 2, 37073 Göttingen			Alles	Laktoseintoleranz	Dessert	Ja	Ja
        50	Melina Busch		w	melina.b@email.de			Hermann-Rein-Straße 3, 37075 Göttingen	Medizin		Vegan		Dessert	Ja	Nein
        51	Erik Lange	Marie John	m	erik.l@email.de	w	marie.john@email.de	Tammannstraße 4, 37077 Göttingen			Alles		Egal	Ja	Ja
        52	Vanessa König		d	vanessa.k@email.de			Geiststraße 10, 37073 Göttingen	Psychologie		Vegetarisch		Vorspeise	Ja	Ja
        53	Justus Ritter	Hannah Thiel	m	justus.r@email.de	w	hannah.thiel@email.de	Friedländer Weg 2, 37085 Göttingen			Alles		Dessert	Ja	Ja
        54	Chiara Adam		w	chiara.a@email.de			Berliner Straße 28, 37073 Göttingen	BWL		Alles		Hauptspeise	Ja	Ja
        55	Fabian Neumann	Lina Beck	m	fabian.n@email.de	w	lina.beck@email.de	Rote Straße 15, 37073 Göttingen			Alles		Egal	Ja	Ja
        56	Alina Wagner		w	alina.w@email.de			Jüdenstraße 33, 37073 Göttingen	Germanistik		Vegetarisch		Vorspeise	Ja	Ja
        57	Jannik Schulz	Lara Simon	m	jannik.s@email.de	w	lara.simon@email.de	Dahlmannstraße 1, 37073 Göttingen			Alles		Dessert	Ja	Ja
        58	Ronja Wolf		w	ronja.w@email.de			Gartenstraße 25, 37073 Göttingen	Biologie		Vegan	Glutenfrei	Hauptspeise	Ja	Ja
        59	Sebastian Horn		m	sebastian.h@email.de			Lotzestraße 16, 37083 Göttingen	Sportwissenschaft		Alles		Egal	Nein	Nein
        60	Emilia Graf	Leo Keller	w	emilia.g@email.de	m	leo.keller@email.de	Planckstraße 1, 37073 Göttingen			Alles		Vorspeise	Ja	Ja
        61	Henry Vogel		m	henry.v@email.de			Calsowstraße 5, 37085 Göttingen	Geschichte		Vegetarisch		Dessert	Ja	Ja
        62	Mila Roth	Ben Kruse	w	mila.r@email.de	m	ben.kruse@email.de	Baurat-Gerber-Straße 2, 37073 Göttingen			Alles		Egal	Ja	Ja
        63	Jakob Ernst		d	jakob.e@email.de			Walkemühlenweg 11, 37083 Göttingen	Philosophie		Alles		Vorspeise	Ja	Nein
        64	Ida Klein	Noah Schreiber	w	ida.k@email.de	m	noah.schreiber@email.de	Wagnerstraße 3, 37085 Göttingen			Vegetarisch	Nussallergie	Vorspeise	Ja	Ja
        65	Matthias Koch		m	matthias.k@email.de			Albaniplatz 8, 37073 Göttingen	Theologie		Alles		Egal	Ja	Ja
        66	Leni Bauer	Moritz Seidel	w	leni.b@email.de	m	moritz.seidel@email.de	Tilsiter Straße 19, 37083 Göttingen			Alles		Dessert	Ja	Ja
        67	Timo Lorenz		m	timo.l@email.de			Friedrich-Hund-Platz 1, 37077 Göttingen	Physik		Vegetarisch		Hauptspeise	Ja	Ja
        68	Ella Winter	Finn Brandt	w	ella.w@email.de	m	finn.brandt@email.de	Maschmühlenweg 4, 37073 Göttingen			Alles		Egal	Ja	Ja
        69	Hannes Bergmann		m	hannes.b@email.de			Godehardstraße 11, 37081 Göttingen	Soziologie		Vegan		Vorspeise	Ja	Ja
        70	Frida Peters	Leon Pohl	w	frida.p@email.de	m	leon.pohl@email.de	Königsstieg 90, 37085 Göttingen			Alles	Laktoseintoleranz	Dessert	Ja	Ja
        71	Theo Grimm		m	theo.g@email.de			Grüner Weg 7, 37075 Göttingen	Forstwissenschaft		Alles		Hauptspeise	Ja	Ja
        72	Lilly Hahn	David Engel	w	lilly.h@email.de	m	david.engel@email.de	Heinrich-Düker-Weg 12, 37073 Göttingen			Vegetarisch		Egal	Ja	Ja
        73	Aaron Franke		m	aaron.f@email.de			Kreuzbergring 2, 37075 Göttingen	Informatik		Alles		Vorspeise	Ja	Ja
        74	Maja Neumann	Julia Sommer	w	maja.n@email.de	w	julia.sommer@email.de	Am Menzelberg 8, 37081 Göttingen			Alles		Dessert	Ja	Ja
        75	Bruno Schubert		d	bruno.s@email.de			Leinestraße 21, 37073 Göttingen	Geographie		Vegetarisch		Hauptspeise	Ja	Ja
        76	Zoe Friedrich	Tom Berg	w	zoe.f@email.de	m	tom.berger@email.de	Am Klausberge 10, 37077 Göttingen	Chemie		Egal			Ja	Ja
        77	Oskar Keller		m	oskar.k@email.de			Hospitalstraße 7, 37073 Göttingen	Geschichte		Vegan	Fruktoseintoleranz	Vorspeise	Ja	Nein
        78	Hannah Jäger	Elias May	w	hannah.j@email.de	m	e... (truncated on purpose)
        '''


        def parse_mapping(text):
            """Parse lines and return mapping email -> {'kitchen_available': bool|None, 'main_course_possible': bool|None}

            Strategy: split by whitespace, find the first token containing '@' as the email. The last two tokens are assumed
            to be the kitchen and main course flags (Ja/Nein). This is tolerant to varying column counts.
            """
            mapping = {}
            def to_bool(v):
                v = (v or '').strip().lower()
                if v in ("ja", "yes", "y", "true", "1"): return True
                if v in ("nein", "no", "n", "false", "0"): return False
                return None

            for line in text.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                # find email token
                email = None
                for tok in parts:
                    if '@' in tok:
                        email = tok.strip().lower()
                        break
                if not email:
                    continue
                # assume last two tokens are kitchen and main course
                if len(parts) >= 2:
                    kitchen_token = parts[-2]
                    main_token = parts[-1]
                else:
                    kitchen_token = ''
                    main_token = ''
                mapping[email] = {
                    'kitchen_available': to_bool(kitchen_token),
                    'main_course_possible': to_bool(main_token),
                }
            return mapping


        def main():
            if len(sys.argv) < 2:
                print("Usage: update_users_from_csv.py /path/to/dinnerhopping.users.json")
                sys.exit(1)
            json_path = Path(sys.argv[1])
            if not json_path.exists():
                print(f"File not found: {json_path}")
                sys.exit(1)

            # timestamped backup to avoid overwriting previous backups
            ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
            bak_path = json_path.with_suffix(json_path.suffix + f'.bak.{ts}')
            shutil.copy2(json_path, bak_path)
            print(f"Backup created at {bak_path}")

            with json_path.open('r', encoding='utf-8') as f:
                data = json.load(f)

            mapping = parse_mapping(CSV_TEXT)

            email_to_index = {}
            for i, obj in enumerate(data):
                email = obj.get('email')
                if email:
                    email_to_index[email.lower()] = i

            modified = 0
            not_found = []
            for email, vals in mapping.items():
                idx = email_to_index.get(email)
                if idx is None:
                    not_found.append(email)
                    continue
                obj = data[idx]
                changed = False
                if vals['kitchen_available'] is not None and obj.get('kitchen_available') != vals['kitchen_available']:
                    obj['kitchen_available'] = vals['kitchen_available']
                    changed = True
                if vals['main_course_possible'] is not None and obj.get('main_course_possible') != vals['main_course_possible']:
                    obj['main_course_possible'] = vals['main_course_possible']
                    changed = True
                if changed:
                    modified += 1

            with json_path.open('w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            print(f"Modified entries: {modified}")
            if not_found:
                print(f"Emails not found ({len(not_found)}):")
                for e in not_found:
                    print(' -', e)


        if __name__ == '__main__':
            main()
