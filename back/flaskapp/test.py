import os
import shutil
import subprocess
from time import sleep
from flask import Flask, render_template, request, redirect, url_for, flash
import libvirt
import psutil

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
    hypervisor_info = get_hypervisor_info()
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
    return render_template('vms.html', vms=vms, hypervisor_info=hypervisor_info)


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


def sauvegarder_vm(nom_vm, chemin_sauvegarde):
    try:
        # Connexion à l'hyperviseur
        conn = libvirt.open('qemu:///system')
        if conn is None:
            print("Échec de la connexion à l'hyperviseur")
            return False

        # Obtenir le domaine de la VM
        domaine = conn.lookupByName(nom_vm)
        if domaine is None:
            print(f"VM '{nom_vm}' non trouvée")
            conn.close()
            return False

        # Sauvegarder l'état actuel de la VM dans le fichier spécifié
        domaine.save(chemin_sauvegarde)
        print(f"État de la VM '{nom_vm}' sauvegardé avec succès dans {chemin_sauvegarde}")
        conn.close()
        return True

    except libvirt.libvirtError as e:
        print(f"Erreur libvirt : {e}")
        return False


def restaurer_vm(chemin_sauvegarde):
    try:
        # Connexion à l'hyperviseur
        conn = libvirt.open('qemu:///system')
        if conn is None:
            print("Échec de la connexion à l'hyperviseur")
            return False

        # Restaurer la VM depuis le fichier de sauvegarde
        conn.restore(chemin_sauvegarde)
        print(f"VM restaurée depuis le fichier {chemin_sauvegarde}")
        conn.close()
        return True

    except libvirt.libvirtError as e:
        print(f"Erreur libvirt : {e}")
        return False
    
def get_hypervisor_info():
    # Exemple pour récupérer les informations du système
    try:
        # Nom de l'hyperviseur (on utilise une commande virsh pour KVM)
        hypervisor_name = subprocess.check_output("hostname", shell=True).decode().strip()

        # Version (avec une commande spécifique à l'hyperviseur)
        hypervisor_version = subprocess.check_output("virsh --version", shell=True).decode().strip()

        # Nombre total de CPU
        cpus = psutil.cpu_count(logical=False)

        # Mémoire totale (en MiB)
        memory = psutil.virtual_memory().total // (1024 * 1024)

        # Espace disque disponible (en MiB)
        storage = psutil.disk_usage('/').free // (1024 * 1024)

        return {
            'name': hypervisor_name,
            'version': hypervisor_version,
            'cpus': cpus,
            'memory': memory,
            'storage': storage
        }
    except Exception as e:
        print(f"Error retrieving hypervisor info: {e}")
        return {}

# Sauvegarde l'état d'une VM
@app.route('/save_vm/<string:vm_name>', methods=['POST'])
def save_vm(vm_name):
    chemin_sauvegarde = os.path.join(DISK_STORAGE_PATH, f"{vm_name}.sav")  # Chemin de sauvegarde pour la VM
    if sauvegarder_vm(vm_name, chemin_sauvegarde):
        flash(f"L'état de la VM '{vm_name}' a été sauvegardé avec succès.", 'success')
    else:
        flash(f"Erreur lors de la sauvegarde de la VM '{vm_name}'.", 'error')
    return redirect(url_for('list_vms'))

# Restaure l'état d'une VM
@app.route('/restore_vm/<string:vm_name>', methods=['POST'])
def restore_vm(vm_name):
    chemin_sauvegarde = os.path.join(DISK_STORAGE_PATH, f"{vm_name}.sav")  # Chemin de sauvegarde pour la VM
    if restaurer_vm(chemin_sauvegarde):
        flash(f"La VM '{vm_name}' a été restaurée avec succès.", 'success')
    else:
        flash(f"Erreur lors de la restauration de la VM '{vm_name}'.", 'error')
    return redirect(url_for('list_vms'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)