terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  default = "ap-south-1"
}

variable "instance_type" {
  default = "t3.medium"
}

variable "key_name" {
  description = "EC2 key pair name for SSH access"
  type        = string
}

variable "postgres_password" {
  description = "PostgreSQL password"
  type        = string
  sensitive   = true
}

variable "jwt_secret" {
  description = "JWT secret (64+ chars)"
  type        = string
  sensitive   = true
}

variable "admin_email" {
  default = "admin@example.com"
}

variable "admin_password" {
  description = "Admin user password"
  type        = string
  sensitive   = true
}

variable "s3_bucket" {
  description = "S3 bucket for frame storage (in humanarchive account)"
  type        = string
  default     = "demo-hand-tracking-bucket"
}

variable "s3_access_key" {
  description = "AWS access key for S3 bucket (humanarchive account)"
  type        = string
  sensitive   = true
}

variable "s3_secret_key" {
  description = "AWS secret key for S3 bucket (humanarchive account)"
  type        = string
  sensitive   = true
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_security_group" "human_archive" {
  name        = "human-archive-sg"
  description = "Security group for Human Archive backend"

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Backend API"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "human-archive"
  }
}

resource "aws_instance" "backend" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = var.key_name
  vpc_security_group_ids = [aws_security_group.human_archive.id]

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
  }

  user_data = <<-USERDATA
#!/bin/bash
set -ex
exec > /var/log/user-data.log 2>&1

echo "Installing Docker"
apt-get update
apt-get install -y ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

systemctl enable docker
systemctl start docker

echo "Cloning repo"
git clone https://github.com/MdSadiqMd/Human-Archive.git /opt/human-archive
cd /opt/human-archive

echo "Creating .env file"
cat > /opt/human-archive/.env << 'ENDENV'
POSTGRES_USER=ha_user
POSTGRES_PASSWORD=${postgres_password}
JWT_SECRET=${jwt_secret}
ADMIN_EMAIL=${admin_email}
ADMIN_PASSWORD=${admin_password}
BACKEND_PORT=8080
S3_BUCKET=${s3_bucket}
AWS_REGION=${aws_region}
AWS_ACCESS_KEY_ID=${s3_access_key}
AWS_SECRET_ACCESS_KEY=${s3_secret_key}
ENDENV

echo "Starting services"
docker compose -f docker-compose.prod.yml up -d --build

echo "Deployment complete"
USERDATA

  tags = {
    Name = "human-archive-backend"
  }
}

output "instance_id" {
  value = aws_instance.backend.id
}

output "public_ip" {
  value = aws_instance.backend.public_ip
}

output "public_dns" {
  value = aws_instance.backend.public_dns
}

output "api_url" {
  value = "http://${aws_instance.backend.public_ip}:8080"
}

output "frontend_env" {
  value = "VITE_API_URL=http://${aws_instance.backend.public_ip}:8080"
}

output "ssh_command" {
  value = "ssh -i ~/.ssh/${var.key_name}.pem ubuntu@${aws_instance.backend.public_ip}"
}
