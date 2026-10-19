from pathlib import Path
p=Path('/api/db/seeds.rb');s=p.read_text().replace('  Language.unscoped.delete_all\n','').replace('Language.create(', 'Language.unscoped.find_or_initialize_by(id: language[:id]).update!(');p.write_text(s)
p=Path('/api/db/languages/active.rb');p.write_text(p.read_text()+'''\n@languages << {id: 100, name: "C++20 (GCC 12.2.0)", source_file: "main.cpp", compile_cmd: "/usr/bin/g++-12 -std=c++20 -O2 %s main.cpp", run_cmd: "./a.out", is_archived: false}\n''')
