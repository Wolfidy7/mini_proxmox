import os
from time import sleep
from flask import Flask, render_template, request, redirect, url_for, flash
import libvirt

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Nécessaire pour utiliser les messages flash
ISO_STORAGE_PATH = '/var/lib/libvirt/iso' 
DISK_STORAGE_PATH = '/var/lib/libvirt/images' 

# Connexion à l'hyperviseur
def get_connection():
    try:
        return libvirt.open('qemu:///system')
    except libvirt.libvirtError as e:
        print(f'Erreur de connexion : {e}')
        return None

 #Méthode pour obtenir la taille du fichier disque
def get_disk_size(disk_path):
    try:
        return os.path.getsize(disk_path) / (1024 * 1024)  # Convertir en Mo
    except OSError:
        return None  # Retourne None si le fichier n'existe pas ou qu'il y a une erreur
    
# Affiche la liste des VMs avec RAM, CPU et espace disque
@app.route('/vms', methods=['GET'])
def list_vms():
    conn = get_connection()
    if conn is None:
        flash("Échec de la connexion à l'hyperviseur.", 'error')
        return redirect(url_for('list_vms'))

    vms = {}
    for domain in conn.listAllDomains():  # Utiliser listAllDomains pour obtenir toutes les VMs
        state, _ = domain.state()  # Obtenir l'état de la VM
        state_description = {
            libvirt.VIR_DOMAIN_NOSTATE: 'Pas d\'état',
            libvirt.VIR_DOMAIN_RUNNING: 'En cours d\'exécution',
            libvirt.VIR_DOMAIN_BLOCKED: 'Bloquée',
            libvirt.VIR_DOMAIN_PAUSED: 'Suspendue',
            libvirt.VIR_DOMAIN_SHUTDOWN: 'Arrêt en cours',
            libvirt.VIR_DOMAIN_SHUTOFF: 'Arrêtée',
            libvirt.VIR_DOMAIN_CRASHED: 'Crashée',
            libvirt.VIR_DOMAIN_PMSUSPENDED: 'Suspendue (PMS)',
        }.get(state, 'État inconnu')  # Valeur par défaut si état non reconnu

        # Initialiser les valeurs par défaut pour les ressources
        ram_size = "Non disponible"
        vcpu_count = "Non disponible"

        # Obtenir RAM et vCPU uniquement si la VM est en cours d'exécution
        if state == libvirt.VIR_DOMAIN_RUNNING:
            ram_size = domain.maxMemory() / 1024  # RAM en Mo
            vcpu_count = domain.maxVcpus()  # Nombre de vCPU

        # Chemin de l'image disque
        disk_file_path = os.path.join(DISK_STORAGE_PATH, f"{domain.name()}.qcow2")
        disk_size = get_disk_size(disk_file_path)  # Taille du disque en Mo

        vms[domain.name()] = {
            'state': state_description,
            'ram': f"{ram_size} Mo" if isinstance(ram_size, (int, float)) else ram_size,
            'vcpu': vcpu_count,
            'disk': f"{disk_size} Mo" if disk_size is not None else 'Non disponible'
        }

    conn.close()
    return render_template('vms.html', vms=vms)

@app.route('/create_vm', methods=['GET', 'POST'])
def create_vm():
    conn = None  # Initialiser conn à None
    if request.method == 'POST':
        try:
            # Récupérer les informations envoyées par le formulaire
            vm_name = request.form['vm_name']
            ram_size = int(request.form['ram_size']) * 1024  # Convertir en Ko
            vcpu_count = int(request.form['vcpu_count'])
            iso_file = request.files['iso_file']
            
            # Générer le chemin pour le fichier disque de la VM
            disk_file_path = os.path.join(DISK_STORAGE_PATH, f"{vm_name}.qcow2")
            
            # Vérifier l'existence du fichier disque
            if not os.path.exists(disk_file_path):
                flash(f"Le fichier disque {disk_file_path} n'existe pas.", 'error')
                return redirect(url_for('create_vm'))
            
            # Enregistrer le fichier ISO si fourni
            if iso_file and iso_file.filename.endswith('.iso'):
                iso_file_path = os.path.join(ISO_STORAGE_PATH, iso_file.filename)
                iso_file.save(iso_file_path)
            else:
                flash('Le fichier téléchargé n\'est pas un fichier ISO valide.', 'error')
                return redirect(url_for('create_vm'))

            # Créer la définition XML pour la VM
            vm_xml = f"""
            <domain type='kvm'>
                <name>{vm_name}</name>
                <memory>{ram_size}</memory>
                <vcpu>{vcpu_count}</vcpu>
                <os>
                    <type arch='x86_64' machine='pc-i440fx-2.9'>hvm</type>
                    <boot dev='cdrom'/>
                    <boot dev='hd'/>
                </os>
                <devices>
                    <disk type='file' device='disk'>
                        <driver name='qemu' type='qcow2'/>
                        <source file='{disk_file_path}'/>
                        <target dev='vda' bus='virtio'/>
                    </disk>
                    <disk type='file' device='cdrom'>
                        <driver name='qemu' type='raw'/>
                        <source file='{iso_file_path}'/>
                        <target dev='hdc' bus='ide'/>
                        <readonly/>
                    </disk>
                    <interface type='network'>
                        <mac address='52:54:00:6b:29:66'/>
                        <source network='default'/>
                        <model type='virtio'/>
                    </interface>
                    <graphics type='vnc' port='-1' listen='0.0.0.0'/>
                </devices>
            </domain>
            """

            # Connexion et création de la VM
            conn = get_connection()
            if conn is None:
                flash('Échec de la connexion à l\'hyperviseur.', 'error')
                return redirect(url_for('create_vm'))

            # Création de la VM à partir du XML
            conn.createXML(vm_xml, 0)
            flash(f'La VM "{vm_name}" a été créée avec succès !', 'success')
        
        except KeyError as e:
            flash(f'Erreur dans le formulaire: {str(e)}', 'error')
            return redirect(url_for('create_vm'))
        
        except libvirt.libvirtError as e:
            flash(f'Erreur lors de la création de la VM : {str(e)}', 'error')
        
        finally:
            if conn:
                conn.close()
        
        return redirect(url_for('list_vms'))
    
    return render_template('create_vm.html')




# Démarre une VM
@app.route('/start_vm/<string:vm_name>', methods=['POST'])
def start_vm(vm_name):
    conn = get_connection()
    if conn is None:
        flash('Échec de la connexion à l\'hyperviseur lors du démarrage de la VM.', 'error')
        return redirect(url_for('list_vms'))
    
    try:
        domain = conn.lookupByName(vm_name)
        state, _ = domain.state()  # Récupérer l'état de la VM
        if state == libvirt.VIR_DOMAIN_RUNNING:
            flash(f'La VM "{vm_name}" est déjà en cours d\'exécution.', 'info')
        elif state == libvirt.VIR_DOMAIN_PAUSED:
            try:
                domain.resume()  # Reprendre la VM
                flash(f'La VM "{vm_name}" a été reprise avec succès.', 'success')
            except libvirt.libvirtError as e:
                flash(f'Erreur lors de la reprise de la VM : {e}', 'error')
        else:
            try:
                domain.create()  # Démarrer la VM
                flash(f'La VM "{vm_name}" a été démarrée avec succès.', 'success')
            except libvirt.libvirtError as e:
                flash(f'Erreur lors du démarrage de la VM : {e}', 'error')
    except libvirt.libvirtError as e:
        flash(f'Erreur lors de l\'accès à la VM : {e}', 'error')
    finally:
        conn.close()
    return redirect(url_for('list_vms'))

# Arrête une VM
@app.route('/stop_vm/<string:vm_name>', methods=['POST'])
def stop_vm(vm_name):
    conn = get_connection()
    if conn is None:
        flash('Échec de la connexion à l\'hyperviseur lors de l\'arrêt de la VM.', 'error')
        return redirect(url_for('list_vms'))
    
    try:
        domain = conn.lookupByName(vm_name)
        state, _ = domain.state()  # Récupérer l'état de la VM
        if state == libvirt.VIR_DOMAIN_SHUTOFF:
            flash(f'La VM "{vm_name}" est déjà arrêtée.', 'info')
        elif state == libvirt.VIR_DOMAIN_RUNNING:
            domain.destroy()  # Utilise shutdown pour un arrêt propre
            flash(f'La VM "{vm_name}" a été arrêtée avec succès.', 'success')
        else:
            flash(f'La VM "{vm_name}" est dans un état qui ne permet pas l\'arrêt.', 'info')
    except libvirt.libvirtError as e:
        flash(f'Erreur lors de l\'arrêt de la VM : {e}', 'error')
    finally:
        conn.close()
    return redirect(url_for('list_vms'))

# Redémarre une VM
@app.route('/restart_vm/<string:vm_name>', methods=['POST'])
def restart_vm(vm_name):
    conn = get_connection()
    if conn is None:
        flash('Échec de la connexion à l\'hyperviseur lors du redémarrage de la VM.', 'error')
        return redirect(url_for('list_vms'))

    try:
        domain = conn.lookupByName(vm_name)
        state, _ = domain.state()

        # Si la VM est en cours d'exécution, tente de l'arrêter
        if state == libvirt.VIR_DOMAIN_RUNNING:
            domain.destroy()  # Arrêt propre de la VM
            flash(f'La VM "{vm_name}" est en cours d\'arrêt pour le redémarrage.', 'info')

            # Attente pour s'assurer que la VM s'arrête complètement avant de la redémarrer
            sleep(3)

        # Redémarrage de la VM
        domain.create()
        flash(f'La VM "{vm_name}" a été redémarrée avec succès.', 'success')
    
    except libvirt.libvirtError as e:
        flash(f'Erreur lors du redémarrage de la VM : {e}', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('list_vms'))


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
