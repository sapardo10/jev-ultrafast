import 'package:flutter/material.dart';

// Probe for jev's native Android driver: the hamburger is an icon-only IconButton with no tooltip or Semantics
// label, exactly like the unlabeled menu button that blocks mapas mobile.
void main() => runApp(const ProbeApp());

class ProbeApp extends StatelessWidget {
  const ProbeApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Jev Probe',
        home: Scaffold(
          appBar: AppBar(
            title: const Text('Probe'),
            leading: Builder(
              builder: (context) => IconButton(
                icon: const Icon(Icons.menu),
                onPressed: () => Scaffold.of(context).openDrawer(),
              ),
            ),
          ),
          drawer: Drawer(
            child: ListView(children: [
              const DrawerHeader(child: Text('Menu')),
              Builder(
                builder: (context) => ListTile(
                  title: const Text('Settings'),
                  onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(
                    builder: (_) => Scaffold(
                      appBar: AppBar(title: const Text('Settings opened')),
                      body: const Center(child: Text('Settings opened')),
                    ),
                  )),
                ),
              ),
            ]),
          ),
          body: const Center(child: Text('Home')),
        ),
      );
}
